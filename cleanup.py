"""
Stop a running app.py safely. Cross-platform: POSIX scans /proc, Windows uses
tasklist/taskkill. Never matches its own command line.
    python3 cleanup.py
"""
import os, sys, signal, subprocess, time

WORKDIR = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(WORKDIR, "app.py")


def _is_target(argv, pid):
    """True only for a python process running THIS project's app.py.
    Matches the exact 'app.py' basename as a token (not any substring ending in
    'app.py'), and confirms the process belongs to this project directory."""
    if not argv:
        return False
    exe = os.path.basename(argv[0]).lower()
    if not exe.startswith("python"):
        return False
    # never match the cleanup tool itself
    if any(os.path.basename(a) == "cleanup.py" for a in argv):
        return False
    # the script must be exactly app.py (by basename token, not substring)
    script_tokens = [a for a in argv[1:] if os.path.basename(a) == "app.py"]
    if not script_tokens:
        return False
    # confirm this is OUR app.py: either an explicit path to this WORKDIR/app.py,
    # or a bare 'app.py' launched from this project's working directory.
    for tok in script_tokens:
        if os.path.isabs(tok) or os.sep in tok:
            if os.path.realpath(tok) == os.path.realpath(APP):
                return True
        else:
            try:
                cwd = os.path.realpath(f"/proc/{pid}/cwd")
            except OSError:
                cwd = None
            if cwd == os.path.realpath(WORKDIR):
                return True
    return False


def _terminate(pid):
    """SIGTERM, wait briefly, then SIGKILL only if still alive."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(20):              # up to ~2s
        time.sleep(0.1)
        try:
            os.kill(pid, 0)          # still alive?
        except ProcessLookupError:
            return                   # exited cleanly after SIGTERM
        except PermissionError:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def stop_posix():
    me, parent = os.getpid(), os.getppid()
    killed = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) in (me, parent):
            continue
        ipid = int(pid)
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().decode(
                "utf-8", "replace").split("\x00")
            argv = [a for a in argv if a]
            if _is_target(argv, pid):
                _terminate(ipid); killed.append(pid)
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            pass
    return killed

def stop_windows():
    """Find python processes running app.py via WMIC/tasklist and kill them."""
    killed = []
    try:
        out = subprocess.run(
            ["wmic", "process", "where",
             "name like 'python%' and commandline like '%app.py%'",
             "get", "processid"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit() and int(line) != os.getpid():
                subprocess.run(["taskkill", "/F", "/PID", line], capture_output=True)
                killed.append(line)
    except Exception as e:
        print("could not enumerate processes:", e)
    return killed

if __name__ == "__main__":
    killed = stop_windows() if os.name == "nt" else stop_posix()
    print("stopped server pids:", killed or "none")
