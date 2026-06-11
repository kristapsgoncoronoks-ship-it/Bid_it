"""
Stop a running app.py safely. Cross-platform: POSIX scans /proc, Windows uses
tasklist/taskkill. Never matches its own command line.
    python3 cleanup.py
"""
import os, sys, signal, subprocess

def stop_posix():
    me, parent = os.getpid(), os.getppid()
    killed = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) in (me, parent):
            continue
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode().replace("\x00", " ").strip()
            if cmd in ("python3 app.py", "python app.py") or cmd.endswith(("app.py",)) and "python" in cmd and "cleanup" not in cmd:
                os.kill(int(pid), signal.SIGKILL); killed.append(pid)
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
