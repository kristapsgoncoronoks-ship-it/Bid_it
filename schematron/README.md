# Vendored EN 16931 / PEPPOL BIS Billing 3.0 schematrons

These files are **source artefacts** (NOT gitignored). `einvoice_validate.py` compiles them
at runtime with a real XSLT 2.0 engine (`saxonche`) and validates the UBL e-invoices that
`invoicing.einvoice_xml` produces. We vendor them so validation runs **offline**, reproducibly
and pinned to a known revision.

## The official schematrons (OpenPEPPOL)

| file | source |
|------|--------|
| `CEN-EN16931-UBL.sch`    | the EN 16931 model bound to UBL |
| `PEPPOL-EN16931-UBL.sch` | the PEPPOL BIS Billing 3.0 rules |

From <https://github.com/OpenPEPPOL/peppol-bis-invoice-3> (`rules/sch/`), © OpenPEPPOL AISBL,
licensed under the **European Union Public Licence (EUPL) v1.2**.

Both declare `queryBinding="xslt2"`. `lxml.isoschematron` only ships the XSLT **1.0** ISO
skeleton, so it would silently drop every xslt2-bound rule (the BR-CO total/category
arithmetic, codelist look-ups, …) and report a **false PASS** — hence the real XSLT2 engine.

## The ISO Schematron XSLT2 skeleton (Schematron/schematron)

| file | role |
|------|------|
| `iso_dsdl_include.xsl`                  | pipeline stage 1 — resolve includes |
| `iso_abstract_expand.xsl`               | pipeline stage 2 — expand abstract patterns |
| `iso_svrl_for_xslt2.xsl`                | pipeline stage 3 — emit an SVRL XSLT2 stylesheet |
| `iso_schematron_skeleton_for_saxon.xsl` | the Saxon skeleton `iso_svrl_for_xslt2.xsl` imports |

From <https://github.com/Schematron/schematron> (`trunk/schematron/code/`), the reference ISO
Schematron implementation — distributed under permissive open terms (Rick Jelliffe / Academia
Sinica Computing Centre, "free to use").

## Pipeline

For each `.sch`, `einvoice_validate._compile` runs the three ISO stages in order
(`include` → `abstract_expand` → `svrl_for_xslt2`) to produce an SVRL-emitting XSLT2
stylesheet, compiles it once and caches it process-wide. `validate_ubl` then runs the cached
stylesheet over the invoice XML and parses the SVRL `failed-assert` / `successful-report`
findings.
