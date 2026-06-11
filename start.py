"""
ONE-CLICK LAUNCHER - start the app and open it in the browser.
First run shows a friendly setup page (create admin) - no command line needed.
    python start.py        (or double-click start.bat / start.command)
Installs any missing dependencies automatically on first run.
"""
import os, sys, subprocess, time, threading, webbrowser
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

def ensure_deps():
    try:
        import flask, openpyxl, cryptography  # noqa
        return
    except ImportError:
        print("Installing required packages (first run only)...")
        for extra in ([], ["--break-system-packages"], ["--user"]):
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                                "-r", "requirements.txt", *extra], capture_output=True)
            if r.returncode == 0:
                print("  packages installed."); return
        print("  Could not auto-install. Run:  pip install -r requirements.txt")

def open_browser(url):
    time.sleep(2.0)
    try: webbrowser.open(url)
    except Exception: pass

if __name__ == "__main__":
    ensure_deps()
    import tls
    scheme = "https" if tls.resolve() else "http"
    url = f"{scheme}://localhost:8050"
    print(f"\n  Fleet Fuel system starting...")
    print(f"  Open your browser to:  {url}")
    print(f"  (the browser should open automatically; press Ctrl+C here to stop)\n")
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    from app import app
    ctx, _ = tls.build_context()
    if ctx:
        app.config.update(SESSION_COOKIE_SECURE=True)
        app.run(host="127.0.0.1", port=8050, ssl_context=ctx)
    else:
        app.run(host="127.0.0.1", port=8050)
