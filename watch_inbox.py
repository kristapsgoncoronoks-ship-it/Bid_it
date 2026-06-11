"""
INBOX WATCHER - auto-import invoice batches dropped into a folder, so import
doesn't need a manual upload. Polls inbox/ for new PDF/ZIP files, extracts a DRAFT,
and queues it for review in the app (it never auto-commits - human still confirms).

    python3 watch_inbox.py                 # poll ./inbox every 30s
    python3 watch_inbox.py --once          # single pass (for cron)

For email intake: point your mail client / a tool like getmail or a Power Automate
flow to save attachments into inbox/, then this picks them up. Kept deliberately
simple and infra-free; nothing here needs network in this environment.
"""
import os, sys, time, json, shutil
WORKDIR = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(WORKDIR, "inbox")
QUEUE = os.path.join(WORKDIR, "review_queue")
DONE  = os.path.join(WORKDIR, "inbox_processed")

def pass_once():
    import extract as EX
    for d in (INBOX, QUEUE, DONE): os.makedirs(d, exist_ok=True)
    n = 0
    for fn in os.listdir(INBOX):
        if not fn.lower().endswith((".pdf",".zip")): continue
        path = os.path.join(INBOX, fn)
        data = open(path,"rb").read()
        try:
            draft = EX.extract(data, fn)
            draft.pop("_pdf_bytes", None)
            json.dump(draft, open(os.path.join(QUEUE, fn+".json"),"w"), indent=1, default=str)
            shutil.move(path, os.path.join(DONE, fn))
            print(f"queued {fn}: {len(draft.get('lines',[]))} draft lines -> review in app")
            n += 1
        except Exception as e:
            print(f"error on {fn}: {e}")
    return n

if __name__ == "__main__":
    if "--once" in sys.argv:
        print(f"{pass_once()} file(s) processed")
    else:
        print("watching inbox/ (Ctrl+C to stop)")
        while True:
            pass_once(); time.sleep(30)
