"""
INBOX WATCHER - auto-import invoice batches dropped into a folder, so import
doesn't need a manual upload. Polls a watched drop folder for new PDF/ZIP files and
ENQUEUES each into the durable intake queue (intake.db, via waiting_room) — the same
"waiting room" the server drains in the background (app.start_intake_worker). It never
auto-commits — extraction produces only a DRAFT and a human still confirms it in the app.

    python3 watch_inbox.py                 # poll the drop dir every 30s
    python3 watch_inbox.py --once          # single pass (for cron)

For email intake: point your mail client / a tool like getmail or a Power Automate
flow to save attachments into the drop dir, then this picks them up. Kept deliberately
simple and infra-free; nothing here needs network in this environment.

The watched drop dir is DISTINCT from the queue's internal byte store (inbox/): the
queue keeps a copy of every accepted upload under inbox/<sha>.bin, so if the watcher
scanned inbox/ it would re-scan the queue's own files. Override the drop dir with
WATCH_INBOX_DIR (default inbox_drop/). Enqueue is idempotent by content hash, so a
re-scan of the same bytes is harmless; on a successful enqueue the source file is moved
to a DONE subdir so it isn't re-read.
"""
import os, sys, time, shutil
import applog
import waiting_room

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# Distinct from waiting_room.INBOX (the queue's <sha>.bin store) so the scan dir is
# never the same as the queue's internal store.
INBOX = os.environ.get("WATCH_INBOX_DIR", os.path.join(WORKDIR, "inbox_drop"))
DONE  = os.path.join(INBOX, "processed")

log = applog.get("watch_inbox")


def pass_once():
    """Scan the drop dir once; enqueue each new file into the durable intake queue
    and move it to the DONE subdir after a successful enqueue. Per-file failures are
    logged (not raised) so one bad file can't stall the rest. Returns the count
    enqueued."""
    os.makedirs(INBOX, exist_ok=True)
    os.makedirs(DONE, exist_ok=True)
    n = 0
    for fn in sorted(os.listdir(INBOX)):
        if not fn.lower().endswith((".pdf", ".zip")):
            continue
        path = os.path.join(INBOX, fn)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
            jid, status = waiting_room.enqueue(data, fn, user="inbox-watcher")
            # enqueue copied the bytes durably (inbox/<sha>.bin); move the source out
            # of the scan dir so it isn't re-read on the next pass.
            shutil.move(path, os.path.join(DONE, fn))
            log.info("enqueued %s -> job %s (%s)", fn, jid, status)
            n += 1
        except Exception as e:
            log.warning("failed to enqueue %s: %s", fn, e)
    return n


if __name__ == "__main__":
    if "--once" in sys.argv:
        print(f"{pass_once()} file(s) enqueued")
    else:
        print(f"watching {INBOX} (Ctrl+C to stop)")
        while True:
            pass_once(); time.sleep(30)
