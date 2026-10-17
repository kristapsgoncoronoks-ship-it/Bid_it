# V01b — Supervised PDF parsing

Implemented on the development branch; pending CI/review, not deployed.

Invoice capture, receipt PDF text/OCR, bank-statement PDF text/OCR and public
embedded-XML probing now execute PDF libraries in a fresh Linux child process.
Outbound generation of application-owned PDFs retains its existing path.

The parent uses private temporary files, bounded JSON-lines output, a 90-second
wall deadline, a new process group and unconditional group termination/reaping.
Children inherit no database, JWT, SMTP or storage credentials and run in a private
working directory. Kernel limits: 60 CPU seconds, 1536 MiB address space, 4 MiB
output/file size and no core dumps. Input uses the central configured upload cap;
decoded text is capped at 2 MiB; attachments at 16 blobs / 4 MiB combined. Excess
pages fail explicitly at 50 rather than returning a silently truncated invoice.
OCR rendering fits 40 million pixels / 20,000 pixels per edge, reducing scale for
large scans; normal A4 pages retain 300 DPI. These are fixed conservative budgets,
not a promise to process every 50-page scanned document before the wall deadline.
Library allocation/decompression before a decoded-size check is contained by the
child memory/CPU/wall limits. This is resource isolation, not a syscall/network sandbox.

Progress observations cross the child protocol into the existing parent-owned
persistence sink. Timeout/crash/resource failure becomes a classified capture
failure; upload and email failure handling retains source evidence and recovery.

Validation: 78 focused tests passed across process/PDF/OCR/Factur-X, statement,
upload, email and upload-cap modules. The final process module has 9 passing tests
(including two additional real kernel CPU/memory probes). Tests cover runaway
termination/reaping, oversized output, attachment count/decoded bytes, page cap,
progress, absent inherited secrets, persisted timeout and a subsequent successful
upload. Whole-app mypy passed (414 files); Ruff passed. No production probe or
live deployment was performed. Full-head CI and final-image checks remain required.
