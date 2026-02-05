#!/usr/bin/env python3
"""Wait for all Juju units to reach active/idle state.

Usage: wait_for_juju_idle.py [--timeout SECONDS] [--interval SECONDS]

Polls `juju status --format json` and counts units not in active/idle state.
Exits 0 when all units are ready, exits 1 on timeout.
"""

import argparse
import json
import subprocess
import sys
import time


def get_not_ready_count():
    """Return count of units not in active/idle state, or -1 on error."""
    try:
        result = subprocess.run(
            ["juju", "status", "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return -1

    count = 0
    apps = data.get("applications", {})
    for app_info in apps.values():
        units = app_info.get("units", {})
        for unit_info in units.values():
            workload = unit_info.get("workload-status", {}).get("current", "")
            agent = unit_info.get("juju-status", {}).get("current", "")
            if workload != "active" or agent != "idle":
                count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description="Wait for Juju units to settle")
    parser.add_argument(
        "--timeout", type=int, default=600, help="Timeout in seconds (default: 600)"
    )
    parser.add_argument(
        "--interval", type=int, default=10, help="Poll interval in seconds (default: 10)"
    )
    args = parser.parse_args()

    print(f"==> Waiting for all units to settle (timeout: {args.timeout}s)")
    elapsed = 0

    while elapsed < args.timeout:
        not_ready = get_not_ready_count()

        if not_ready == 0:
            print("==> All units are active/idle.")
            return 0

        print(f"    {not_ready} unit(s) not ready, waiting...")
        time.sleep(args.interval)
        elapsed += args.interval

    print(f"ERROR: Timed out waiting for units after {args.timeout}s", file=sys.stderr)
    subprocess.run(["juju", "status"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
