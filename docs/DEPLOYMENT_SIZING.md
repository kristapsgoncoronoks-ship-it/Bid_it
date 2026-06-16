# Deployment sizing — what server to run

Companion to `SCALING.md`. That document is the **ladder** for when you outgrow one box;
this one answers the more common question: **"what do I actually buy to run this
smoothly?"** for a normal single-tenant install processing a modest invoice volume.

The headline: this app is **light**. The web tier (Flask + waitress + WAL-SQLite) sits
well under 1 GB of RAM and near-idle CPU. The only components that move the needle on
sizing are the **extraction path** (CPU/RAM-spiky *only* if you run local OCR or a local
VLM) and **disk growth** (the `documents/` vault + backup snapshots). Everything else is
noise at the volumes a single transport group produces.

---

## Reference workload: ~100 invoices/day

100 invoices/day is ~12–13 per working hour, ~1 every five minutes — even arriving in a
few batch uploads it is a trivial throughput. The transaction databases grow by ~37k rows
per year, nothing for SQLite. **This stays firmly at `SCALING.md` Stage 0 (one tuned
box).** You do **not** need Postgres, a worker fleet, or a load balancer at this scale —
adding them only adds operational surface area and failure modes.

---

## Recommended specification

| Scenario | vCPU | RAM | Disk (SSD/NVMe) | When |
|----------|:----:|:---:|:---------------:|------|
| **Baseline** | 2 | 4 GB | 80–120 GB | Structured e-invoice + deterministic parser + *cloud* AI fallback only (no local OCR/VLM) |
| **Recommended** | **4** | **8 GB** | **150 GB** | The sweet spot — headroom for OCR, Excel/SAF-T exports, backups, and a few concurrent operators. **Buy this.** |
| **Local OCR / local VLM** | 4–8 | 16 GB | 200 GB | You run Tesseract OCR (`EXTRACT_OCR_BACKEND`) or a local Docling/VLM extractor on-prem |

**OS:** Ubuntu Server LTS (24.04) recommended; any stable Linux is fine. Windows is
supported (waitress is cross-platform), but Linux behind nginx is the smoother production
path. Use an **SSD/NVMe** — never spinning disk — because WAL write latency matters.

A GPU is **not** worth it at this volume: if you want fully on-prem extraction, CPU-based
Docling (~0.5–2 s/page) handles 100 invoices/day with ease; reserve GPUs for the day you
process thousands of *scanned* pages per day.

---

## Why these numbers (where the load actually is)

- **CPU** — structured/Factur-X parsing and the deterministic parsers are milliseconds per
  document. Cloud AI extraction is network-bound (local CPU near-idle). The *only*
  CPU-heavy path is **local OCR/VLM**, and even a scanned batch at 100/day finishes in
  seconds. The extra cores buy burst headroom for a batch upload landing while someone
  runs an Excel/SAF-T export or a backup is zipping.
- **RAM** — Flask + waitress (4 threads) + SQLite-WAL is < 1 GB. The 8 GB is buffer for
  OCR/PDF rasterisation (`pdf2image` holds page bitmaps in memory) and OS page cache for
  fast DB reads. 4 GB is safe **only** if you will never run local OCR/VLM.
- **Disk is the real growth driver, not compute.** At ~250 KB/PDF, 100 invoices/day is
  **~9 GB/year** in `documents/` (SHA-256 dedup trims repeats), plus periodic backup
  snapshots that bundle the DBs + `documents/` + audit CSVs. The databases themselves stay
  tiny. Budget generously and prefer a **separate volume** for `documents/` and `backups/`
  so vault/backup I/O never competes with the live DB.

---

## Configuration for a smooth single box (all built-in — no rewrite)

| What | How |
|------|-----|
| Production server | `python serve.py` (waitress). Bump `THREADS=8` only if many concurrent operators (default 4 is plenty). |
| Database | Keep **SQLite + WAL** — `db_tuning.py` tunes it automatically. Postgres is unnecessary here. |
| HTTPS | Terminate TLS with **nginx** (or IIS on Windows) in front; `serve.py` binds HTTP locally for the proxy to wrap. |
| Background work | Default `FFS_ROLE=all` — the in-process intake worker drains the queue; no separate worker node needed. |
| Backups | Enable the scheduler (admin setting `backup_interval_hours`) + off-machine backup sync; the daily `verify_backup` / `verify_docs` integrity checks are effectively free at this scale. |
| Secrets / KEK | `FFS_KEK_KEY` (or the `local` provider) for the envelope-encrypted credential vault; keep `.secret_key` on the box. |

Relevant env vars are catalogued in `SCALING.md` → *Configuration reference*.

---

## Concrete host options (pick one)

| Provider | Instance | Spec | Notes |
|----------|----------|------|-------|
| AWS | `t3.large` (`t3.xlarge` if local OCR) | 2–4 vCPU / 8–16 GB | EBS **gp3** ~150 GB; snapshot for DR |
| Azure | `B2ms` (`B4ms` if local OCR) | 2–4 vCPU / 8–16 GB | OS disk + a separate data disk for `documents/` |
| Hetzner | `CX32` / `CX42` | 4–8 vCPU / 8–16 GB | + a Volume; excellent value, ample for this |
| DigitalOcean | Basic/Premium 4 vCPU / 8 GB | 4 vCPU / 8 GB | + a Block Storage volume |
| On-prem | NUC-class mini-PC | 4-core / 16 GB / NVMe | Put it on a **UPS**; back up off-machine |

---

## When to climb the ladder (none of these are near 100/day)

Move to `SCALING.md` Stage 1+ only when you observe:

1. **Sustained SQLite write contention** — you'd be at *thousands* of invoices/day. → Stage 1: Postgres.
2. **`documents/` outgrowing local disk** — → Stage 2: object store (S3/Azure Blob via `document_vault.py`).
3. **A need for HA / zero-downtime / horizontal throughput** — → Stage 3–4: split `web`/`worker` roles behind a load balancer with a shared `FFS_SECRET_KEY`.

At 100 invoices/day you are an order of magnitude below every one of these triggers.

**Bottom line:** a **4 vCPU / 8 GB / 150 GB NVMe** Linux box behind nginx, running
SQLite-WAL with daily verified backups, runs 100 invoices/day with room to spare.
