"""Watchdog: launch jietu as a child process, restart on crash."""
import os
import subprocess
import sys
import time
from pathlib import Path

# Exit code jietu uses for intentional user quit — do NOT restart on this.
CLEAN_EXIT = 0
RESTART_DELAY = 3   # seconds between crash restarts
MAX_RESTARTS = 20   # give up after this many consecutive crashes


def _child_cmd() -> list[str]:
    """Build the command to run jietu in child mode (no console on Windows)."""
    if sys.platform == "win32":
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        exe = str(pythonw) if pythonw.exists() else sys.executable
    else:
        exe = sys.executable
    return [exe, "-m", "jietu", "--child"]


def main():
    restarts = 0
    while True:
        proc = subprocess.run(_child_cmd(), env=os.environ.copy())
        if proc.returncode == CLEAN_EXIT:
            break
        restarts += 1
        if restarts > MAX_RESTARTS:
            break
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    main()
