"""Self-upgrade helper — runs as a DETACHED process so it can replace jietu
while jietu (the app + watchdog) is being terminated.

Flow when run via `pythonw -m jietu.upgrade`:
  1. wait briefly for the launching app to quit
  2. kill any remaining jietu processes (app + watchdog) so files unlock
  3. pip force-reinstall the latest version into the SAME Python env
  4. silently relaunch the watchdog
"""
from __future__ import annotations
import os
import subprocess
import sys
import time

REPO = "git+https://github.com/fendouww/jietu.git"


def _python_exes() -> tuple[str, str]:
    """(python.exe, pythonw.exe) next to the current interpreter."""
    d = os.path.dirname(sys.executable)
    py = os.path.join(d, "python.exe")
    pyw = os.path.join(d, "pythonw.exe")
    return (py if os.path.exists(py) else sys.executable,
            pyw if os.path.exists(pyw) else sys.executable)


def _kill_running(exclude_pid: int):
    """Terminate other jietu processes (not this upgrader)."""
    if sys.platform == "win32":
        ps = (
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -in 'python.exe','pythonw.exe' -and "
            "$_.CommandLine -match 'jietu' -and "
            "$_.CommandLine -notmatch 'jietu.upgrade' -and "
            "$_.ProcessId -ne %d } | ForEach-Object { "
            "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        ) % exclude_pid
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
        )
    else:
        subprocess.run(["pkill", "-f", "jietu.watchdog"], capture_output=True)
        subprocess.run(["pkill", "-f", "-m jietu"], capture_output=True)


def _relaunch():
    _py, pyw = _python_exes()
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            [pyw, "-m", "jietu.watchdog"],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    else:
        subprocess.Popen([pyw, "-m", "jietu.watchdog"])


def run():
    time.sleep(1.5)                 # let the launching app exit
    _kill_running(os.getpid())
    time.sleep(0.8)                 # wait for file locks to release
    py, _pyw = _python_exes()
    result = subprocess.run(
        [py, "-m", "pip", "install", "--upgrade", "--force-reinstall",
         "--no-deps", REPO],
        capture_output=True,
    )
    if result.returncode != 0:
        target = os.environ.get("JIETU_UPGRADE_VERSION", "")
        if target:
            try:
                from jietu.updater import mark_upgrade_failed
                mark_upgrade_failed(target)
            except Exception:
                pass
        return
    _relaunch()


def spawn_detached(target_version: str = ""):
    """Launch this module as a detached process that survives the app exiting."""
    env = os.environ.copy()
    if target_version:
        env["JIETU_UPGRADE_VERSION"] = target_version
    _py, pyw = _python_exes()
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            [pyw, "-m", "jietu.upgrade"],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            env=env,
        )
    else:
        subprocess.Popen([pyw, "-m", "jietu.upgrade"],
                         start_new_session=True, env=env)


if __name__ == "__main__":
    run()
