#!/usr/bin/env python3
"""
CLI tool to fetch Claude Code usage quota from a running TwiCC instance.

Connects to the TwiCC WebSocket, retrieves the latest usage snapshot,
and displays quota utilization with burn rates.

Configuration is read from the TwiCC .env file (~/.twicc/.env or TWICC_DATA_DIR).

Usage:
    uv run ./twicc-quota.py              # One-shot: print current quota and exit
    uv run ./twicc-quota.py --watch      # Stay connected, print on every update
    uv run ./twicc-quota.py --json       # Output raw JSON message
    uv run ./twicc-quota.py --compact    # Single-line summary
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import websockets


# ---------------------------------------------------------------------------
# Config resolution (mirrors twicc.paths without Django dependency)
# ---------------------------------------------------------------------------

def get_data_dir() -> Path:
    env_value = os.environ.get("TWICC_DATA_DIR", "").strip()
    if env_value:
        return Path(env_value).resolve()
    return Path.home() / ".twicc"


def read_env(env_path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file (no quoting, no interpolation)."""
    env_vars: dict[str, str] = {}
    if not env_path.is_file():
        return env_vars
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env_vars[key.strip()] = value.strip()
    return env_vars


def get_config() -> tuple[str, int, str]:
    """Return (token, port, host) from .env + environment."""
    data_dir = get_data_dir()
    env_vars = read_env(data_dir / ".env")

    token = os.environ.get("TWICC_API_TOKEN") or env_vars.get("TWICC_API_TOKEN", "")
    port = int(os.environ.get("TWICC_PORT") or env_vars.get("TWICC_PORT", "3500"))
    host = os.environ.get("TWICC_HOST", "localhost")

    return token, port, host


# ---------------------------------------------------------------------------
# Burn rate computation (mirrors frontend/src/utils/usage.js)
# ---------------------------------------------------------------------------

FIVE_HOUR_SECS = 5 * 3600
SEVEN_DAY_SECS = 7 * 24 * 3600


def temporal_pct(resets_at_iso: str | None, window_secs: int) -> float | None:
    """Compute how far through a quota window we are (0–100%)."""
    if not resets_at_iso:
        return None
    resets_at = datetime.fromisoformat(resets_at_iso)
    now = datetime.now(timezone.utc)
    remaining = (resets_at - now).total_seconds()
    elapsed = window_secs - remaining
    if window_secs <= 0:
        return None
    pct = (elapsed / window_secs) * 100
    return max(0.0, min(100.0, pct))


def burn_rate(utilization: float | None, time_pct: float | None) -> float | None:
    """Compute burn rate = utilization / temporal_pct. >1 means on pace to hit cap."""
    if utilization is None or time_pct is None or time_pct <= 0:
        return None
    return (utilization / 100) / (time_pct / 100)


def level_for(util: float | None, br: float | None) -> str:
    """Return a severity level string."""
    if util is None:
        return "inactive"
    if util >= 100:
        return "CRITICAL"
    if br is not None and br >= 1.15:
        return "CRITICAL"
    if br is not None and br >= 0.9:
        return "WARNING"
    return "ok"


def format_remaining(resets_at_iso: str | None) -> str:
    """Human-readable time until reset."""
    if not resets_at_iso:
        return "—"
    resets_at = datetime.fromisoformat(resets_at_iso)
    now = datetime.now(timezone.utc)
    delta = resets_at - now
    total_secs = int(delta.total_seconds())
    if total_secs < 0:
        return "resetting..."
    hours, remainder = divmod(total_secs, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

# ANSI colors
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def color_for_level(lvl: str) -> str:
    if lvl == "CRITICAL":
        return RED
    if lvl == "WARNING":
        return YELLOW
    return GREEN


def format_bar(utilization: float | None, width: int = 20) -> str:
    """Render a text progress bar."""
    if utilization is None:
        return DIM + "—" + RESET
    filled = int(utilization / 100 * width)
    filled = max(0, min(width, filled))
    bar = "█" * filled + "░" * (width - filled)
    return bar


def format_quota_line(
    label: str, util: float | None, resets_at: str | None, window_secs: int,
) -> str:
    """Format a single quota line with bar, percentage, burn rate, and remaining time."""
    if util is None:
        return f"  {label + ':':<22} {DIM}no data{RESET}"

    t_pct = temporal_pct(resets_at, window_secs)
    br = burn_rate(util, t_pct)
    lvl = level_for(util, br)
    clr = color_for_level(lvl)
    remaining = format_remaining(resets_at)

    br_str = f"×{br:.2f}" if br is not None else "—"
    bar = format_bar(util)

    return (
        f"  {label + ':':<22} {clr}{bar} {util:5.1f}%{RESET}"
        f"  burn: {clr}{br_str:<6}{RESET}"
        f"  resets in {remaining}"
    )


def format_cost_line(label: str, cost_data: dict | None) -> str:
    """Format a period cost summary."""
    if not cost_data:
        return f"  {label + ':':<22} {DIM}no data{RESET}"

    spent = cost_data.get("spent", 0)
    est_period = cost_data.get("estimated_period", 0)
    est_monthly = cost_data.get("estimated_monthly", 0)
    capped = cost_data.get("capped", False)

    cap_marker = f" {RED}(capped){RESET}" if capped else ""
    return (
        f"  {label + ':':<22} "
        f"${spent:.2f} spent → "
        f"~${est_period:.2f}/period, "
        f"~${est_monthly:.1f}/month{cap_marker}"
    )


def display_usage(usage: dict) -> None:
    """Print formatted usage information."""
    fetched = usage.get("fetched_at", "?")
    if fetched and fetched != "?":
        try:
            dt = datetime.fromisoformat(fetched).astimezone()
            fetched = dt.strftime("%H:%M:%S")
        except ValueError:
            pass

    print(f"\n{BOLD}Claude Code Quota{RESET}  {DIM}(fetched {fetched}){RESET}\n")

    # 5-hour quota
    print(format_quota_line(
        "5h window", usage.get("five_hour_utilization"),
        usage.get("five_hour_resets_at"), FIVE_HOUR_SECS,
    ))

    # 7-day quotas
    print(format_quota_line(
        "7d global", usage.get("seven_day_utilization"),
        usage.get("seven_day_resets_at"), SEVEN_DAY_SECS,
    ))
    print(format_quota_line(
        "7d opus", usage.get("seven_day_opus_utilization"),
        usage.get("seven_day_opus_resets_at"), SEVEN_DAY_SECS,
    ))
    print(format_quota_line(
        "7d sonnet", usage.get("seven_day_sonnet_utilization"),
        usage.get("seven_day_sonnet_resets_at"), SEVEN_DAY_SECS,
    ))

    # Optional quotas (only show if present)
    if usage.get("seven_day_oauth_apps_utilization") is not None:
        print(format_quota_line(
            "7d oauth apps", usage.get("seven_day_oauth_apps_utilization"),
            usage.get("seven_day_oauth_apps_resets_at"), SEVEN_DAY_SECS,
        ))
    if usage.get("seven_day_cowork_utilization") is not None:
        print(format_quota_line(
            "7d cowork", usage.get("seven_day_cowork_utilization"),
            usage.get("seven_day_cowork_resets_at"), SEVEN_DAY_SECS,
        ))

    # Period costs
    period_costs = usage.get("period_costs")
    if period_costs:
        print()
        print(format_cost_line("5h costs", period_costs.get("five_hour")))
        print(format_cost_line("7d costs", period_costs.get("seven_day")))

    # Extra usage
    if usage.get("extra_usage_is_enabled"):
        limit = usage.get("extra_usage_monthly_limit", 0)
        used = usage.get("extra_usage_used_credits", 0)
        util = usage.get("extra_usage_utilization")
        print(f"\n  {'Extra usage:':<22} ${used:.2f} / ${limit:.2f} ({util:.1f}%)" if util else "")

    print()


def display_compact(usage: dict) -> None:
    """Print a compact one-line summary."""
    parts = []

    for label, key, resets_key, window in [
        ("5h", "five_hour_utilization", "five_hour_resets_at", FIVE_HOUR_SECS),
        ("7d", "seven_day_utilization", "seven_day_resets_at", SEVEN_DAY_SECS),
    ]:
        util = usage.get(key)
        if util is None:
            continue
        t_pct = temporal_pct(usage.get(resets_key), window)
        br = burn_rate(util, t_pct)
        br_str = f"×{br:.2f}" if br is not None else ""
        remaining = format_remaining(usage.get(resets_key))
        parts.append(f"{label}: {util:.1f}% {br_str} ({remaining})")

    period_costs = usage.get("period_costs", {})
    five_h = period_costs.get("five_hour", {})
    if five_h:
        parts.append(f"~${five_h.get('estimated_monthly', 0):.0f}/mo")

    print(" | ".join(parts))


# ---------------------------------------------------------------------------
# WebSocket client
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> None:
    token, port, host = get_config()

    if not token:
        print("Error: No API token found. Start TwiCC at least once to auto-generate it,", file=sys.stderr)
        print("or set TWICC_API_TOKEN in your .env file.", file=sys.stderr)
        sys.exit(1)

    # Build WebSocket URL with subscribe filter for efficiency
    ws_url = f"ws://{host}:{port}/ws/?token={token}&subscribe=usage_updated"

    try:
        async with websockets.connect(ws_url) as ws:
            while True:
                raw = await ws.recv()
                msg = json.loads(raw)

                if msg.get("type") != "usage_updated":
                    continue

                if not msg.get("success"):
                    print(f"Warning: usage fetch failed (reason: {msg.get('reason')})", file=sys.stderr)
                    if not args.watch:
                        sys.exit(1)
                    continue

                usage = msg.get("usage", {})

                if args.json:
                    print(json.dumps(usage, indent=2))
                elif args.compact:
                    display_compact(usage)
                else:
                    display_usage(usage)

                if not args.watch:
                    return

    except websockets.exceptions.InvalidStatusCode as e:
        if e.status_code == 403:
            print("Error: Authentication failed. Check your API token.", file=sys.stderr)
        else:
            print(f"Error: WebSocket connection failed (HTTP {e.status_code})", file=sys.stderr)
        sys.exit(1)
    except ConnectionRefusedError:
        print(f"Error: Cannot connect to TwiCC at {host}:{port}. Is it running?", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Claude Code usage quota from a running TwiCC instance.",
    )
    parser.add_argument(
        "--watch", "-w", action="store_true",
        help="Stay connected and print on every update (every ~5 min).",
    )
    parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output raw JSON instead of formatted display.",
    )
    parser.add_argument(
        "--compact", "-c", action="store_true",
        help="Single-line summary.",
    )
    parser.add_argument(
        "--host", default=None,
        help="TwiCC host (default: localhost).",
    )
    parser.add_argument(
        "--port", "-p", type=int, default=None,
        help="TwiCC backend port (default: from .env or 3500).",
    )
    parser.add_argument(
        "--token", "-t", default=None,
        help="API token (default: from .env).",
    )
    args = parser.parse_args()

    # Allow CLI overrides
    if args.host:
        os.environ["TWICC_HOST"] = args.host
    if args.port:
        os.environ["TWICC_PORT"] = str(args.port)
    if args.token:
        os.environ["TWICC_API_TOKEN"] = args.token

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
