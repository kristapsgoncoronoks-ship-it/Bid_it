"""
PRODUCTION SERVER LAUNCHER - cross-platform.
  python3 serve.py            -> waitress (works on Windows AND Linux, pure Python)

Uses waitress, a production WSGI server that runs identically on Windows and Linux
(unlike gunicorn, which needs fork() and is POSIX-only). Serves HTTPS when a
certificate is configured (cert.pem/key.pem or TLS_* env - see tls.py); otherwise
HTTP on localhost. For Linux you may still prefer gunicorn behind nginx; on Windows,
waitress (optionally behind IIS/nginx) is the standard choice.

    pip install waitress
    python3 serve.py
"""
import os
HOST = os.environ.get("BIND_HOST", "127.0.0.1")
PORT = int(os.environ.get("BIND_PORT", "8050"))

def main():
    from app import app, start_backup_scheduler, start_intake_worker
    import tls
    start_backup_scheduler()   # automatic backups per the admin-set schedule
    start_intake_worker()      # drain the document waiting room in the background
    ctx, desc = tls.build_context()
    try:
        from waitress import serve as wserve
    except ImportError:
        print("waitress not installed (pip install waitress); "
              "falling back to the built-in dev server.")
        if ctx:
            app.config.update(SESSION_COOKIE_SECURE=True)
            app.run(host=HOST, port=PORT, ssl_context=ctx)
        else:
            app.run(host=HOST, port=PORT)
        return
    if ctx:
        # waitress doesn't terminate TLS itself; for HTTPS either run behind a
        # TLS proxy (nginx/IIS) OR use the dev server's ssl. We document the proxy
        # path and here bind HTTP locally for the proxy to wrap.
        app.config.update(SESSION_COOKIE_SECURE=True)
        print(f" * waitress on http://{HOST}:{PORT} (put a TLS proxy in front - cert: {desc})")
    else:
        print(f" * waitress on http://{HOST}:{PORT}")
    wserve(app, host=HOST, port=PORT, threads=int(os.environ.get("THREADS", "4")))

if __name__ == "__main__":
    main()
