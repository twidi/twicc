#!/usr/bin/env python
"""
Development process controller for TWICC.

Manages frontend (npm run dev) and backend (uv run ./run.py) processes
as independent background daemons with logging.
"""
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent
DEVCTL_DIR = PROJECT_ROOT / ".devctl"
LOGS_DIR = DEVCTL_DIR / "logs"
PIDS_DIR = DEVCTL_DIR / "pids"

# Expected ports (important for HTTP tunnels)
FRONTEND_PORT = 5173
BACKEND_PORT = 3500

# Process configurations
PROCESSES = {
    "front": {
        "name": "Frontend (Vite)",
        "cmd": ["npm", "run", "dev"],
        "cwd": PROJECT_ROOT / "frontend",
        "log": LOGS_DIR / "frontend.log",
        "pid": PIDS_DIR / "frontend.pid",
        "port": FRONTEND_PORT,
    },
    "back": {
        "name": "Backend (Django)",
        "cmd": ["uv", "run", "./run.py"],
        "cwd": PROJECT_ROOT,
        "log": LOGS_DIR / "backend.log",
        "pid": PIDS_DIR / "backend.pid",
        "port": BACKEND_PORT,
    },
}


def ensure_dirs():
    """Create .devctl directory structure if needed."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PIDS_DIR.mkdir(parents=True, exist_ok=True)


def is_running(proc_key: str) -> tuple[bool, int | None]:
    """Check if a process is running. Returns (is_running, pid)."""
    pid_file = PROCESSES[proc_key]["pid"]
    if not pid_file.exists():
        return False, None

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 0)  # Signal 0 just checks if process exists
        return True, pid
    except OSError:
        # Process doesn't exist, clean up stale pid file
        pid_file.unlink()
        return False, None


def verify_port(proc_key: str, log_start_pos: int, timeout: float = 5.0) -> bool:
    """Verify that process started on the expected port by checking NEW log lines only."""
    config = PROCESSES[proc_key]
    expected_port = config.get("port")
    if not expected_port:
        return True  # No port check needed

    log_file = config["log"]
    start_time = time.time()

    # Pattern to match the port in logs
    # Frontend: "Local:   http://localhost:5173/"
    # Backend: "Uvicorn running on http://0.0.0.0:3500"
    port_patterns = [
        rf"localhost:{expected_port}",
        rf"0\.0\.0\.0:{expected_port}",
        rf"127\.0\.0\.1:{expected_port}",
    ]

    while time.time() - start_time < timeout:
        if log_file.exists():
            # Only read NEW content since process started
            with open(log_file) as f:
                f.seek(log_start_pos)
                new_content = f.read()

            if new_content:
                for pattern in port_patterns:
                    if re.search(pattern, new_content):
                        return True
                # Check if wrong port was used (Vite fallback)
                wrong_port_match = re.search(r"localhost:(\d+)/", new_content)
                if wrong_port_match:
                    actual_port = int(wrong_port_match.group(1))
                    if actual_port != expected_port:
                        print(f"    WARNING: Started on port {actual_port} instead of {expected_port}!")
                        print(f"    Port {expected_port} may be in use. Check with: lsof -i :{expected_port}")
                        return False
        time.sleep(0.3)

    print(f"    WARNING: Could not verify port {expected_port} (timeout)")
    return False


def start(proc_key: str) -> bool:
    """Start a process as a detached daemon."""
    config = PROCESSES[proc_key]
    running, pid = is_running(proc_key)

    if running:
        print(f"  {config['name']}: already running (PID {pid})")
        return True

    ensure_dirs()

    # Remember log file position before starting (to only check new lines)
    log_start_pos = 0
    if config["log"].exists():
        log_start_pos = config["log"].stat().st_size

    # Open log file in append mode
    log_file = open(config["log"], "a")

    # Start detached process
    proc = subprocess.Popen(
        config["cmd"],
        cwd=config["cwd"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # Detach from parent
    )

    # Save PID
    config["pid"].write_text(str(proc.pid))
    print(f"  {config['name']}: started (PID {proc.pid})")
    print(f"    Logs: {config['log']}")

    # Verify correct port
    expected_port = config.get("port")
    if expected_port:
        print(f"    Verifying port {expected_port}...", end=" ", flush=True)
        if verify_port(proc_key, log_start_pos):
            print("OK")
        else:
            print("FAILED")
            return False

    return True


def stop(proc_key: str) -> bool:
    """Stop a process and all its children (process group)."""
    config = PROCESSES[proc_key]
    running, pid = is_running(proc_key)

    if not running:
        print(f"  {config['name']}: not running")
        return True

    try:
        # Send SIGTERM to the entire process group (negative PID)
        # This kills npm AND its child processes (node/vite)
        os.killpg(pid, signal.SIGTERM)
        print(f"  {config['name']}: stopped (was PID {pid})")
        config["pid"].unlink(missing_ok=True)
        return True
    except OSError as e:
        # Fallback: try killing just the process if group kill fails
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"  {config['name']}: stopped (was PID {pid})")
            config["pid"].unlink(missing_ok=True)
            return True
        except OSError:
            print(f"  {config['name']}: failed to stop - {e}")
            return False


def status():
    """Show status of all processes."""
    print("Process status:")
    for key, config in PROCESSES.items():
        running, pid = is_running(key)
        if running:
            print(f"  {config['name']}: running (PID {pid})")
        else:
            print(f"  {config['name']}: stopped")


def logs(proc_key: str, lines: int = 50):
    """Show last N lines of logs for a process."""
    config = PROCESSES[proc_key]
    log_file = config["log"]

    if not log_file.exists():
        print(f"  No logs found for {config['name']}")
        print(f"  Expected at: {log_file}")
        return

    # Read last N lines
    with open(log_file) as f:
        all_lines = f.readlines()

    last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

    print(f"=== {config['name']} logs (last {len(last_lines)} lines) ===")
    print(f"=== Log file: {log_file} ===")
    print()
    for line in last_lines:
        print(line, end="")

    if not last_lines:
        print("  (empty)")


def parse_target(target: str | None) -> list[str]:
    """Parse target argument into list of process keys."""
    if target is None or target == "all":
        return list(PROCESSES.keys())
    if target in PROCESSES:
        return [target]
    print(f"Error: Unknown target '{target}'. Use: front, back, or all")
    sys.exit(1)


def print_help():
    """Print help message."""
    help_text = """
devctl.py - Development process controller for TWICC

Manages frontend and backend dev servers as background processes with logging.
Processes run independently and survive after the command exits.

USAGE:
    uv run ./devctl.py <command> [target] [options]

COMMANDS:
    start [target]     Start process(es) in background
    stop [target]      Stop running process(es)
    restart [target]   Stop then start process(es)
    status             Show running status of all processes
    logs <target>      Show recent log output
    help, --help, -h   Show this help message

TARGETS:
    front              Frontend dev server (npm run dev in frontend/)
    back               Backend server (uv run ./run.py)
    all                Both frontend and backend (default for start/stop/restart)

OPTIONS:
    --lines=N          Number of log lines to show (default: 50)

EXAMPLES:
    uv run ./devctl.py start all       # Start both servers
    uv run ./devctl.py start back      # Start only backend
    uv run ./devctl.py stop front      # Stop frontend
    uv run ./devctl.py restart back    # Restart backend
    uv run ./devctl.py status          # Check what's running
    uv run ./devctl.py logs back       # Show last 50 lines of backend logs
    uv run ./devctl.py logs front --lines=100

FILES:
    .devctl/logs/frontend.log    Frontend stdout/stderr
    .devctl/logs/backend.log     Backend stdout/stderr
    .devctl/pids/                 PID files for running processes
"""
    print(help_text.strip())


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "--help", "-h"):
        print_help()
        sys.exit(0)

    command = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else None

    if command == "start":
        targets = parse_target(target)
        print("Starting processes...")
        for key in targets:
            start(key)

    elif command == "stop":
        targets = parse_target(target)
        print("Stopping processes...")
        for key in targets:
            stop(key)

    elif command == "restart":
        targets = parse_target(target)
        print("Restarting processes...")
        for key in targets:
            stop(key)
            start(key)

    elif command == "status":
        status()

    elif command == "logs":
        if target is None:
            print("Error: logs requires a target (front or back)")
            sys.exit(1)
        if target not in PROCESSES:
            print(f"Error: Unknown target '{target}'. Use: front or back")
            sys.exit(1)

        # Parse --lines=N
        lines = 50
        for arg in sys.argv[3:]:
            if arg.startswith("--lines="):
                try:
                    lines = int(arg.split("=")[1])
                except ValueError:
                    print("Error: --lines must be a number")
                    sys.exit(1)

        logs(target, lines)

    else:
        print(f"Error: Unknown command '{command}'")
        print("Commands: start, stop, restart, status, logs")
        sys.exit(1)


if __name__ == "__main__":
    main()
