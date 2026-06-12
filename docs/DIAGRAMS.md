# System schematics

Visual maps of how the Fleet Fuel & VAT Refund System works. Diagrams are written in
[Mermaid](https://mermaid.js.org/) — they render automatically on GitHub and in the
web app. For the narrative version see **[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

## 1. System overview — the six blocks

How a supplier's data travels from a file all the way to a VAT claim and a report,
with the platform services underneath.

```mermaid
flowchart TB
    SRC["Sources<br/>PDF · ZIP · XLSX · CSV · XML · API"]

    subgraph B1["1 · Intake"]
        ING["ingest.py<br/>xlsx / csv / xml / api"]
        EXT["extract.py<br/>PDF/ZIP to draft"]
        WR["waiting_room.py<br/>durable queue + worker"]
    end

    subgraph B3["3 · Engine"]
        CON["consolidate.py"]
        VAL["validate.py<br/>tie-out to invoice totals"]
        BM["build_master.py"]
        HIS["history.py"]
    end

    subgraph B2["2 · Master data"]
        CDB[("customers.db")]
        SDB[("suppliers.db")]
        FDB[("fuel_history.db")]
    end

    subgraph B4["4 · Compliance"]
        VAT["vat_refund.py<br/>claims · locks · fees · vault index"]
        IC["invoice_control.py<br/>receipt / triage"]
        CA["contract_audit.py"]
        DM["doc_mining.py"]
        VCDB[("vat_claims.db<br/>ISOLATED")]
    end

    subgraph B5["5 · Presentation"]
        APP["app.py — Flask<br/>~25 pages · JSON API · Excel"]
        PI["pricing_intelligence.py"]
        AN["anomaly.py"]
        RP["reports.py"]
    end

    subgraph B6["6 · Platform (cross-cutting)"]
        AUTH["auth.py"]
        AUD["audit.py"]
        BK["backup.py"]
        DV["document_vault.py"]
        TLS["tls.py"]
        DB["db.py / db_tuning.py / process_lock.py"]
    end

    SRC --> B1 --> B3
    B2 --> B3
    B3 --> FDB
    B3 --> B4
    FDB -. read on demand .-> B4
    VAT --> VCDB
    B2 --> B5
    B4 --> B5
    B6 -. serves .-> B1
    B6 -. serves .-> B4
    B6 -. serves .-> B5
```

---

## 2. Upload → confirm OK / Bad → process

Every uploaded file is archived and verified on arrival. **OK** is sent for
processing; **Bad** is purged and the whole batch must be re-uploaded.

```mermaid
flowchart TD
    U["User uploads batch<br/>(drag & drop or browse)"] --> A["Archive bytes to data lake<br/>SHA-256"]
    A --> V{"Read back &<br/>re-hash == upload?"}

    V -- "no / empty / error" --> BAD["✗ Upload failed — batch rejected"]
    BAD --> PURGE["Purge bad data<br/>data_lake.delete_locator"]
    PURGE --> LOGF["Imports log: failed"]
    LOGF --> RES["Re-upload the ENTIRE batch"]
    RES --> U

    V -- "yes" --> OK["✓ Upload OK — safely archived"]
    OK --> LOGR["Imports log: received"]
    LOGR --> MODE{"Processing mode"}
    MODE -- "Extract now" --> REV["Parse to draft → human review → confirm"]
    MODE -- "Queue for later" --> Q["Waiting room<br/>background worker"]
    Q --> REV
    REV --> COMMIT[("Committed to books")]
```

---

## 3. Monthly close pipeline

The linear routine that turns a month of supplier files into the master workbook,
loaded history, receipt control, and a backup.

```mermaid
flowchart LR
    D["Drop supplier files"] --> MC["month_config.py"]
    MC --> C["consolidate.py<br/>must PASS every supplier<br/>vs invoice totals"]
    C --> B["build_master.py<br/>monthly workbook"]
    B --> H["history.py<br/>load + trend"]
    H --> IC["invoice_control.py (period)"]
    IC --> BK["backup.py<br/>snapshot + integrity"]
```

---

## 4. VAT refund claim lifecycle (status codes 1A → 5)

A claim per entity × country × period. **1A–1E are system-controlled** — derived from
the adjustable checklist (contract, customer data, bank account, NACE, trade register,
power of attorney + invoices processed, documents attached, period ended). The rest
are advanced manually. Submission locks the invoices (one invoice, one submission);
rejection / appeal / confiscation **keep** the locks — only an explicit withdraw
releases them.

```mermaid
stateDiagram-v2
    state "SYSTEM-CONTROLLED (checklist)" as auto {
        s1A: 1A missing documents
        s1B: 1B docs received — period not ended
        s1C: 1C can be submitted
        s1E: 1E ready to submit
        s1A --> s1B: checklist complete
        s1B --> s1E: period ends
        s1B --> s1C: caveat (e.g. defers to annual)
        s1C --> s1E
    }
    s2: 2 submitted (locks invoices)
    s2A: 2A successfully submitted
    s2B: 2B document request (deadline)
    s3: 3 decision received
    s3A: 3A money received (fee chargeable)
    s3B: 3B rejection (locks kept)
    s3D: 3D under appeal (locks kept)
    s3C: 3C confiscation (locks kept)
    s4: 4 invoice fee / 4A invoice credit
    s5: 5 closed

    [*] --> auto
    s1E --> s2: hard-gated on checklist + period end
    s2 --> s2A
    s2 --> s2B
    s2A --> s3
    s2B --> s3
    s3 --> s3A
    s3 --> s3B
    s3B --> s3D: appeal
    s3D --> s3A: appeal won
    s3 --> s3C
    s3A --> s4: by payout route
    s3C --> s4
    s4 --> s5
    s5 --> [*]
```

The checklist itself is **adjustable** (Customers page) and **system-verified**:

```mermaid
flowchart LR
    RULES["checklist_rules (admin-editable)<br/>contract · customer data · bank ·<br/>NACE · trade register · POA"] --> EVAL["evaluate_checklist()<br/>documents present & not expired<br/>+ data fields verified"]
    CLAIM["+ claim-level checks<br/>invoices processed · docs attached ·<br/>period ended"] --> EVAL
    EVAL --> STAGE{"derive_stage()"}
    STAGE -->|"items missing"| A1["1A"]
    STAGE -->|"complete, period open"| B1["1B"]
    STAGE -->|"complete + ended"| E1["1C / 1E"]
```

---

## 5. Databases — separated on purpose

Each store has one owner module and its own file, so *who we are*, *who they are*, and
*what happened* stay apart — and the monthly rebuild can never corrupt the claim records.

```mermaid
flowchart TB
    APP["app.py + modules"]
    APP --> C[("customers.db<br/>who we are")]
    APP --> S[("suppliers.db<br/>who they are")]
    APP --> F[("fuel_history.db<br/>what happened<br/>(rebuilt monthly)")]
    APP --> V[("vat_claims.db<br/>ISOLATED legal/financial")]
    APP --> SEC[("security.db<br/>users · roles · logs")]
    APP --> DL[("data_lake.db + data_lake/<br/>extraction artifacts")]
    APP --> IN[("intake.db<br/>waiting room")]
    APP --> P[("portal.db<br/>encrypted credentials")]
    APP --> E[("ecb_rates.db<br/>FX cache")]
    V -. reads transactions on demand .-> F
```

---

## 6. Storage — document vault & data lake

Originals and AI-extraction outputs are filed under the same logical tree across
interchangeable backends; every file carries a SHA-256 for dedup and integrity.

```mermaid
flowchart LR
    ORIG["Original PDF / ZIP / scan"] --> VLT["document_vault.py<br/>invoice_vault_path()"]
    AIX["AI extraction output (JSON)"] --> LAKE["data_lake.py<br/>(same backend logic)"]
    VLT --> BE{"Backend"}
    LAKE --> BE
    BE -- local --> L["documents/ · data_lake/"]
    BE -- sharepoint --> SP["sp://drive/item"]
    BE -- ftp / ftps --> FT["ftp://path"]
    L --> SHA["SHA-256 per file<br/>verify_documents()"]
    SP --> SHA
    FT --> SHA
```

---

## 7. Request lifecycle & background workers

A web request is wrapped by audit + security hooks; two background singletons run
under the production server (never on import).

```mermaid
flowchart TB
    REQ["HTTP request"] --> BR["before_request<br/>set audit actor · CSRF · capability check"]
    BR --> H["handler → escaped HTML + /app.js (CSP-safe)"]
    H --> AR["after_request<br/>security headers · reset actor"]
    AR --> RESP["Response"]

    subgraph BG["Background — serve.py / gunicorn_conf.py"]
        SCH["backup scheduler<br/>leader-elected via process_lock"]
        IW["intake worker<br/>drains the waiting room"]
    end
    SCH --> SNAP[("backups/ffs_*.zip")]
    IW --> Q[("intake.db")]
```

---

## 8. Roles & authorization

Two roles; a processor's capabilities are admin-tunable switches. Enforcement is
central, per endpoint.

```mermaid
flowchart LR
    REQ["Request to an endpoint"] --> G["_guard()<br/>PERM_BY_ENDPOINT + auth.has_perm"]
    G -- "capability held" --> OK["Handler runs"]
    G -- "capability missing" --> NO["403 — insufficient permissions"]

    ADMIN["admin<br/>everything + server/user admin"] --> G
    PROC["processor<br/>day-to-day; configurable capabilities;<br/>never server/user admin"] --> G
```

---

## 9. Claim composition & document resolution

A claim is built **from registered invoices** — one row per (invoice, product code), never
a synthetic aggregate. A line that can't be tied to a documented invoice blocks filing, and
is resolved in place by uploading or attaching an already-stored file.

```mermaid
flowchart TB
    TX["transactions (fuel_history.db)"] --> IL["invoice_lines()"]
    REG[("registered invoices")] --> IL
    IL --> ROW["one row per (invoice, product code)<br/>Art. 9 goods code"]
    IL --> UM{"resolves to one<br/>registered invoice?"}
    UM -- no --> TAG["tag UNMATCHED"]
    UM -- yes --> DOC{"document attached<br/>& valid?"}
    TAG --> BLOCK["✗ synthetic / UNMATCHED line<br/>— pack cannot be filed"]
    DOC -- missing --> BLOCK
    DOC -- present --> OK["✓ filable line"]

    BLOCK --> RES{"resolve from /vat"}
    RES -- upload --> ATT["attach_document()<br/>SHA-256 dedup + wrong-attach warning"]
    RES -- "search store" --> FIND["attach_existing()<br/>(data lake + this customer's vault)"]
    FIND --> ATT
    ATT --> DOC
    OK --> LOCK["set_status('submitted')<br/>locks invoices · freezes nothing — rows stay editable (guarded)"]
```

---

## 10. Scaling — one box to a multi-server fleet

The same codebase grows by **configuration, not rewrite**. Node roles split the web
tier from a worker fleet; signed-cookie sessions need no sticky routing (one shared
`FFS_SECRET_KEY`); the database tier moves SQLite → PostgreSQL via `db.py`; documents
move to object storage. Full ladder & env reference in **[SCALING.md](SCALING.md)**.

```mermaid
flowchart TB
    LB["nginx / load balancer"]
    LB --> W1["web #1"]
    LB --> W2["web #2"]
    LB --> WN["web #N"]

    subgraph WEB["FFS_ROLE=web · FFS_SECRET_KEY=shared"]
        W1
        W2
        WN
    end

    subgraph WORK["FFS_ROLE=worker · waiting_room.py --work"]
        K1["worker #1<br/>extraction"]
        KM["worker #M"]
    end

    Q[("shared intake queue<br/>lease-based, reclaim on crash")]
    PG[("PostgreSQL<br/>master + queue + leases<br/>(+ PgBouncer)")]
    OBJ["object storage<br/>SharePoint / S3 / FTPS"]
    BKP["off-machine backup sync"]

    WEB -->|enqueue| Q
    WORK -->|claim| Q
    Q --- PG
    WEB --> PG
    WORK --> PG
    WEB --> OBJ
    WORK --> OBJ
    PG -.-> BKP
    OBJ -.-> BKP
```

> **Status:** node roles, shared sessions, pluggable storage, the lease queue + leader-
> elected scheduler, and the `?`→`%s` paramstyle shim ship today (tested on SQLite). The
> live PostgreSQL cutover (dialect functions, audit-trigger port, `SKIP LOCKED` queue)
> is the remaining work — see SCALING.md "Remaining blockers".

---

## 11. Self-control — errors, backups & data integrity

Every failure is recorded where an admin can see it, and the physical PDF/ZIP store is
verified against its recorded hashes. The loop is *active*: an integrity failure both
raises a red banner and writes an entry to the error log.

```mermaid
flowchart TB
    subgraph ERR["Error self-control"]
        H["handled failure<br/>(_log_exc in except)"] --> EL[("error_log<br/>security.db")]
        U["unhandled exception<br/>(@app.errorhandler)"] --> EL
        APPLOG["applog → logs/app.log<br/>(dev/ops)"]
        H -.-> APPLOG
        EL --> ADM["Admin panel<br/>error log view"]
    end

    subgraph BK["Backup & integrity"]
        SNAP["backup.snapshot()<br/>scheduled or one-click"] --> ZIP[("backups/ffs_*.zip<br/>SHA-256 MANIFEST")]
        VB["Verify last backup"] --> ZIP
        VD["Check document integrity<br/>vat_refund.verify_documents()"] --> DOCS[("documents/ store<br/>re-hash vs invoice_documents.sha256")]
        VB -- mismatch --> FAIL["✗ red banner"]
        VD -- "corrupt / missing" --> FAIL
    end

    FAIL --> EL
```
