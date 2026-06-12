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

## 4. VAT refund claim lifecycle

A claim per entity × country × period. Low-VAT quarters merge dynamically into the
annual claim; submission locks the invoices (one invoice, one submission).

```mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Ready: every invoice has<br/>its vault document
    Draft --> Annual: low-VAT quarter merges
    Annual --> Ready
    Ready --> Submitted: filed via portal (locks invoices)
    Submitted --> Paid: refund received
    Paid --> Fee: service fee invoiced & settled
    Fee --> [*]
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
