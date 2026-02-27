#!/usr/bin/env python
"""
Development process controller for TWICC.

Manages frontend (npm run dev) and backend (uv run ./run.py) processes
as independent background daemons with logging.

Data directory resolution:
    1. In a git worktree: forced to the worktree root (PROJECT_ROOT)
    2. TWICC_DATA_DIR environment variable (if set)
    3. Default: ~/.twicc/

The .env file (ports, password hash, etc.) is read from the data directory.
The backend process receives TWICC_DATA_DIR so it uses the same paths.
"""
import glob
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent
DEVCTL_DIR = PROJECT_ROOT / ".devctl"
PIDS_DIR = DEVCTL_DIR / "pids"

# Default ports
DEFAULT_BACKEND_PORT = 3500
DEFAULT_FRONTEND_PORT = 5173

# Default data directory (same as twicc.paths)
DEFAULT_DATA_DIR = Path.home() / ".twicc"
TWICC_DATA_DIR_ENV = "TWICC_DATA_DIR"


def is_git_worktree() -> bool:
    """Detect if we're running inside a git worktree (not the main working tree).

    Compares git-dir (per-worktree) with git-common-dir (shared).
    In the main worktree they resolve to the same path; in a secondary
    worktree, git-dir points to .git/worktrees/<name>.
    """
    try:
        git_dir = subprocess.check_output(
            ["git", "rev-parse", "--git-dir"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        common_dir = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        git_dir_resolved = os.path.realpath(os.path.join(str(PROJECT_ROOT), git_dir))
        common_dir_resolved = os.path.realpath(os.path.join(str(PROJECT_ROOT), common_dir))
        return git_dir_resolved != common_dir_resolved
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_data_dir() -> Path:
    """Resolve the data directory for this devctl instance.

    Priority:
    1. Git worktree detected → PROJECT_ROOT (always forced, no override)
    2. TWICC_DATA_DIR environment variable (if set)
    3. Default → ~/.twicc/
    """
    if is_git_worktree():
        return PROJECT_ROOT
    env_value = os.environ.get(TWICC_DATA_DIR_ENV, "").strip()
    if env_value:
        return Path(env_value).resolve()
    return DEFAULT_DATA_DIR


# Resolve once at module level
DATA_DIR = get_data_dir()
ENV_FILE = DATA_DIR / ".env"
LOGS_DIR = DATA_DIR / "logs"


def find_available_port(start: int, max_attempts: int = 100) -> int:
    """Find an available port by incrementing from start.

    Tries start, start+1, start+2, ... until a free port is found.
    Raises RuntimeError if no port is available within max_attempts.
    """
    for offset in range(max_attempts):
        port = start + offset
        if port > 65535:
            break
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(
        f"Could not find an available port starting from {start} "
        f"(tried {max_attempts} ports)"
    )


def save_ports_to_env(backend_port: int, frontend_port: int) -> None:
    """Append port configuration to the .env file in the data directory."""
    lines_to_add = [
        "",
        "# Ports auto-configured by devctl (worktree mode)",
        f"TWICC_PORT={backend_port}",
        f"VITE_PORT={frontend_port}",
        "",
    ]
    with open(ENV_FILE, "a") as f:
        f.write("\n".join(lines_to_add))


def copy_database_from_main() -> bool:
    """Copy the database from the main data directory to the worktree.

    Copies data.sqlite and any WAL/SHM files (data.sqlite-wal, data.sqlite-shm).
    Only called in worktree mode when the local database doesn't exist yet.

    Returns True if the copy succeeded (or source doesn't exist), False on error.
    """
    source_db = DEFAULT_DATA_DIR / "db" / "data.sqlite"
    target_db_dir = DATA_DIR / "db"
    target_db = target_db_dir / "data.sqlite"

    if target_db.exists():
        return True  # Already have a local database

    if not source_db.exists():
        print("  No main database found, starting fresh")
        return True  # No source to copy, Django migrate will create it

    print(f"  Copying database from {source_db.parent}...", end=" ", flush=True)

    # Ensure target directory exists
    target_db_dir.mkdir(parents=True, exist_ok=True)

    # Copy data.sqlite and any associated files (-wal, -shm)
    source_pattern = str(source_db) + "*"
    copied_files = []
    for source_file in glob.glob(source_pattern):
        filename = os.path.basename(source_file)
        shutil.copy2(source_file, target_db_dir / filename)
        copied_files.append(filename)

    print("OK")
    print(f"    Files: {', '.join(copied_files)}")
    return True


def clear_local_database() -> None:
    """Delete the local database files in the worktree.

    Removes data.sqlite and any WAL/SHM files so Django migrate
    creates a fresh empty database on next start.
    """
    target_db = DATA_DIR / "db" / "data.sqlite"
    if not target_db.exists():
        return  # Nothing to clear

    removed_files = []
    for db_file in glob.glob(str(target_db) + "*"):
        os.remove(db_file)
        removed_files.append(os.path.basename(db_file))

    print(f"  Cleared local database: {', '.join(removed_files)}")


def load_env_file() -> dict[str, str]:
    """Load environment variables from .env file in the data directory."""
    env_vars = {}
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue
                # Parse KEY=VALUE (strip optional quotes around value)
                if "=" in line:
                    key, _, value = line.partition("=")
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                        value = value[1:-1]
                    env_vars[key.strip()] = value
    return env_vars


def get_ports(auto_find: bool = False) -> tuple[int, int]:
    """Get backend and frontend ports from .env file or defaults.

    If auto_find is True and we're in a worktree, automatically find
    available ports (incrementing from defaults) and save them to .env.
    This avoids port conflicts when running multiple worktrees.
    """
    env_vars = load_env_file()

    backend_port = DEFAULT_BACKEND_PORT
    frontend_port = DEFAULT_FRONTEND_PORT

    has_backend_port = "TWICC_PORT" in env_vars
    has_frontend_port = "VITE_PORT" in env_vars

    if has_backend_port:
        try:
            backend_port = int(env_vars["TWICC_PORT"])
        except ValueError:
            print(f"Warning: Invalid TWICC_PORT in .env, using default {DEFAULT_BACKEND_PORT}")

    if has_frontend_port:
        try:
            frontend_port = int(env_vars["VITE_PORT"])
        except ValueError:
            print(f"Warning: Invalid VITE_PORT in .env, using default {DEFAULT_FRONTEND_PORT}")

    # In worktree mode, auto-find available ports if not already configured
    if auto_find and is_git_worktree() and (not has_backend_port or not has_frontend_port):
        if not has_backend_port:
            backend_port = find_available_port(DEFAULT_BACKEND_PORT + 1)
        if not has_frontend_port:
            frontend_port = find_available_port(DEFAULT_FRONTEND_PORT + 1)
        save_ports_to_env(backend_port, frontend_port)
        print(f"  Auto-configured ports for worktree: backend={backend_port}, frontend={frontend_port}")
        print(f"  Saved to {ENV_FILE}")

    return backend_port, frontend_port


def get_process_config(backend_port: int, frontend_port: int) -> dict:
    """Build process configuration with the given port settings."""
    env_vars = load_env_file()
    dev_hostname = env_vars.get("TWICC_DEV_HOSTNAME", "")

    frontend_env = {
        "BACKEND_PORT": str(backend_port),
    }
    if dev_hostname:
        frontend_env["DEV_HOSTNAME"] = dev_hostname

    return {
        "front": {
            "name": "Frontend (Vite)",
            "cmd": ["npm", "run", "dev", "--", "--port", str(frontend_port)],
            "cwd": PROJECT_ROOT / "frontend",
            "log": LOGS_DIR / "frontend.log",
            "pid": PIDS_DIR / "frontend.pid",
            "port": frontend_port,
            "env": frontend_env,
        },
        "back": {
            "name": "Backend (Django)",
            "cmd": ["uv", "run", "./run.py"],
            "cwd": PROJECT_ROOT,
            "log": LOGS_DIR / "backend.log",
            "pid": PIDS_DIR / "backend.pid",
            "port": backend_port,
            "env": {
                "TWICC_PORT": str(backend_port),
                TWICC_DATA_DIR_ENV: str(DATA_DIR),
                "TWICC_DEBUG": "1",
                # In worktree mode, use a distinct session cookie name to avoid
                # conflicts with the main instance (browsers share cookies across ports).
                **({"TWICC_SESSION_COOKIE": f"sessionid_{backend_port}"} if is_git_worktree() else {}),
            },
        },
    }


def ensure_dirs():
    """Create directory structure for PIDs and logs."""
    PIDS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def is_running(proc_key: str, processes: dict) -> tuple[bool, int | None]:
    """Check if a process is running. Returns (is_running, pid)."""
    pid_file = processes[proc_key]["pid"]
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


def verify_port(proc_key: str, log_start_pos: int, processes: dict, timeout: float = 5.0) -> bool:
    """Verify that process started on the expected port by checking NEW log lines only."""
    config = processes[proc_key]
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


def npm_install(processes: dict) -> bool:
    """Run npm install in the frontend directory if needed."""
    frontend_dir = processes["front"]["cwd"]
    node_modules = frontend_dir / "node_modules"

    if node_modules.exists():
        return True

    print("  Installing frontend dependencies (npm install)...", end=" ", flush=True)
    result = subprocess.run(
        ["npm", "install"],
        cwd=frontend_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("FAILED")
        print(f"    {result.stderr.strip()}")
        return False
    print("OK")
    return True


def start(proc_key: str, processes: dict) -> bool:
    """Start a process as a detached daemon."""
    config = processes[proc_key]
    running, pid = is_running(proc_key, processes)

    if running:
        print(f"  {config['name']}: already running (PID {pid})")
        return True

    ensure_dirs()

    # Ensure frontend dependencies are installed before starting Vite
    if proc_key == "front":
        if not npm_install(processes):
            return False

    # Remember log file position before starting (to only check new lines)
    log_start_pos = 0
    if config["log"].exists():
        log_start_pos = config["log"].stat().st_size

    # Prepare environment with custom variables
    proc_env = os.environ.copy()
    # In worktree mode, purge inherited TWICC_* variables so the child
    # process only sees values from the worktree's .env (loaded by run.py).
    # Without this, variables like TWICC_PASSWORD_HASH from the parent
    # shell would leak into the backend and override the worktree config.
    if is_git_worktree():
        for key in list(proc_env):
            if key.startswith("TWICC_"):
                del proc_env[key]
    if "env" in config:
        proc_env.update(config["env"])

    # Backend: stdout/stderr → DEVNULL (logs go via Python logging FileHandler)
    # Frontend: stdout/stderr → log file (Vite has no Python logger)
    if proc_key == "back":
        stdout_target = subprocess.DEVNULL
        log_file_handle = None
    else:
        log_file_handle = open(config["log"], "a")
        stdout_target = log_file_handle

    # Start detached process
    # stdin must be redirected to DEVNULL to prevent child processes
    # (especially Vite's readline interface for CLI shortcuts) from
    # modifying the parent terminal settings. Without this, killing
    # the process leaves the terminal in a corrupted state (raw mode)
    # because readline doesn't get a chance to restore terminal settings.
    proc = subprocess.Popen(
        config["cmd"],
        cwd=config["cwd"],
        stdin=subprocess.DEVNULL,
        stdout=stdout_target,
        stderr=subprocess.STDOUT,
        env=proc_env,
        start_new_session=True,  # Detach from parent
    )

    # Close the log file handle in the parent process (if opened);
    # the child has its own copy of the file descriptor
    if log_file_handle is not None:
        log_file_handle.close()

    # Save PID
    config["pid"].write_text(str(proc.pid))
    print(f"  {config['name']}: started (PID {proc.pid})")
    print(f"    Logs: {config['log']}")

    # Verify correct port
    expected_port = config.get("port")
    if expected_port:
        print(f"    Verifying port {expected_port}...", end=" ", flush=True)
        if verify_port(proc_key, log_start_pos, processes):
            print("OK")
        else:
            print("FAILED")
            return False

    return True


def stop(proc_key: str, processes: dict) -> bool:
    """Stop a process and all its children (process group)."""
    config = processes[proc_key]
    running, pid = is_running(proc_key, processes)

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


def status(processes: dict):
    """Show status of all processes."""
    backend_port = processes["back"]["port"]
    frontend_port = processes["front"]["port"]

    print(f"Data directory: {DATA_DIR}")
    if is_git_worktree():
        print("  (git worktree detected, using project root)")
    elif os.environ.get(TWICC_DATA_DIR_ENV, "").strip():
        print(f"  (from ${TWICC_DATA_DIR_ENV})")
    else:
        print("  (default)")
    print()

    print(f"Port configuration: frontend={frontend_port}, backend={backend_port}")
    if ENV_FILE.exists():
        print(f"  (from {ENV_FILE})")
    else:
        print("  (defaults, no .env file)")
    print()
    print("Process status:")
    for key, config in processes.items():
        running, pid = is_running(key, processes)
        if running:
            print(f"  {config['name']}: running (PID {pid}) on port {config['port']}")
        else:
            print(f"  {config['name']}: stopped")


def logs(proc_key: str, processes: dict, lines: int = 50):
    """Show last N lines of logs for a process."""
    config = processes[proc_key]
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


def parse_target(target: str | None, processes: dict) -> list[str]:
    """Parse target argument into list of process keys."""
    if target is None or target == "all":
        return list(processes.keys())
    if target in processes:
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
    status             Show running status and port configuration
    logs <target>      Show recent log output
    help, --help, -h   Show this help message

TARGETS:
    front              Frontend dev server (npm run dev)
    back               Backend server (uv run ./run.py)
    all                Both frontend and backend (default for start/stop/restart)

OPTIONS:
    --empty-db         Start with an empty database instead of copying from
                       the main data directory (worktree mode only)
    --lines=N          Number of log lines to show (default: 50)

DATA DIRECTORY:
    All persistent data (database, logs, config) lives in a data directory:
    1. Git worktree detected → project root (always forced)
    2. $TWICC_DATA_DIR environment variable (if set)
    3. Default → ~/.twicc/

    The .env file is read from the data directory.
    The backend process receives TWICC_DATA_DIR automatically.

PORT CONFIGURATION:
    Ports are configured via .env file in the data directory.
    If no .env file exists, defaults are used.

    .env file contents:
        TWICC_PORT=3500   # Backend port (default: 3500)
        VITE_PORT=5173    # Frontend port (default: 5173)

    In git worktrees, if ports are not configured in .env, devctl
    automatically finds available ports by incrementing from
    default+1 (3501→3502→3503... and 5174→5175→5176...) and saves
    them to the worktree's .env file on first start.

DEV HOSTNAME:
    If you access the dev server through a custom hostname (e.g. via a
    reverse proxy or tunnel), set it in the .env file:

        TWICC_DEV_HOSTNAME=myhost.example.com

    This adds the hostname to Vite's allowedHosts so it accepts requests
    for that host. Without this, Vite rejects requests from unknown hosts.

DATABASE (WORKTREE MODE):
    On start/restart in a worktree, devctl automatically copies the
    database from ~/.twicc/db/ if no local database exists yet.
    Use --empty-db to skip the copy and start with a fresh database.

EXAMPLES:
    uv run ./devctl.py start           # Start both servers
    uv run ./devctl.py start back      # Start only backend
    uv run ./devctl.py stop front      # Stop frontend
    uv run ./devctl.py restart back    # Restart backend
    uv run ./devctl.py status          # Check what's running and port config
    uv run ./devctl.py logs back       # Show last 50 lines of backend logs
    uv run ./devctl.py logs front --lines=100
    uv run ./devctl.py start --empty-db    # Worktree: start with fresh database

FILES:
    <data_dir>/.env               Configuration (ports, password hash)
    <data_dir>/db/data.sqlite     SQLite database
    <data_dir>/logs/backend.log   Backend application logs
    <data_dir>/logs/frontend.log  Frontend (Vite) process output
    <data_dir>/logs/sdk/          Raw SDK message logs (per session)
    .devctl/pids/                 PID files for running processes (local)
"""
    print(help_text.strip())


def print_access_urls(frontend_port: int) -> None:
    """Print URLs where the application can be accessed."""
    env_vars = load_env_file()
    dev_hostname = env_vars.get("TWICC_DEV_HOSTNAME", "")

    print()
    print(f"  Access: http://localhost:{frontend_port}")
    if dev_hostname:
        print(f"          https://{dev_hostname}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "--help", "-h"):
        print_help()
        sys.exit(0)

    command = sys.argv[1]

    # Parse positional target and flags from remaining args
    target = None
    empty_db = False
    for arg in sys.argv[2:]:
        if arg == "--empty-db":
            empty_db = True
        elif not arg.startswith("--") and target is None:
            target = arg

    # Validate --empty-db: only allowed in worktree mode
    if empty_db:
        if not is_git_worktree():
            print("Error: --empty-db is only supported in git worktree mode")
            sys.exit(1)
        if command not in ("start", "restart"):
            print("Error: --empty-db is only supported with start/restart commands")
            sys.exit(1)

    # Auto-find ports only on start/restart (may write to .env in worktree mode)
    auto_find = command in ("start", "restart")
    backend_port, frontend_port = get_ports(auto_find=auto_find)
    processes = get_process_config(backend_port, frontend_port)

    if command == "start":
        targets = parse_target(target, processes)
        # In worktree mode: copy DB from main data dir, or clear it if --empty-db
        if is_git_worktree():
            if empty_db:
                clear_local_database()
            else:
                copy_database_from_main()
        print(f"Starting processes (frontend:{frontend_port}, backend:{backend_port})...")
        for key in targets:
            start(key, processes)
        print_access_urls(frontend_port)

    elif command == "stop":
        targets = parse_target(target, processes)
        print("Stopping processes...")
        for key in targets:
            stop(key, processes)

    elif command == "restart":
        targets = parse_target(target, processes)
        # In worktree mode: copy DB from main data dir, or clear it if --empty-db
        if is_git_worktree():
            if empty_db:
                clear_local_database()
            else:
                copy_database_from_main()
        print(f"Restarting processes (frontend:{frontend_port}, backend:{backend_port})...")
        for key in targets:
            stop(key, processes)
            start(key, processes)
        print_access_urls(frontend_port)

    elif command == "status":
        status(processes)

    elif command == "logs":
        if target is None:
            print("Error: logs requires a target (front or back)")
            sys.exit(1)
        if target not in processes:
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

        logs(target, processes, lines)

    else:
        print(f"Error: Unknown command '{command}'")
        print("Commands: start, stop, restart, status, logs")
        sys.exit(1)


if __name__ == "__main__":
    main()
