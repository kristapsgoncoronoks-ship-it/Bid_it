# -*- coding: utf-8 -*-
"""
LATVIAN (lv) TRANSLATION CATALOG — {english_source: latvian}.

A plain Python dict, no external dependency. The English string is the KEY (see
``i18n`` for the rationale). To translate a new string: wrap it in ``t(...)`` at the
call site, then add the ``"English": "Latvian"`` pair to the right section below.

ESCAPING: these values are returned as ordinary ``str`` and escaped by the caller
(``esc(t(...))``) exactly like any other dynamic value — they are NOT HTML-safe markup.

⚠️  LEGAL / VAT TERMINOLOGY REVIEW REQUIRED  ⚠️
The entries grouped under "LEGAL / VAT TERMINOLOGY" below are best-effort
translations of regulated invoice/VAT wording (reverse charge, simplified invoice,
the Art. 226 mandatory fields, etc.). They MUST be reviewed by a Latvian-native
accountant / professional translator before real invoices are issued to customers.
They are listed explicitly in the build report so they can be flagged.
"""

CATALOG = {
    # ============================================================ HEADER / NAV
    # Top-level menu labels — these appear on every page.
    "Home": "Sākums",
    "Intake": "Ievade",
    "Documents": "Dokumenti",
    "Sharing": "Koplietošana",
    "Analytics": "Analītika",
    "VAT & Recovery": "PVN un atgūšana",
    "Master data": "Pamatdati",
    "History": "Vēsture",
    "Tasks": "Uzdevumi",
    "Export": "Eksports",
    "Admin": "Administrēšana",
    "Account": "Konts",
    "Sign out": "Iziet",
    "Invoicing": "Rēķinu izrakstīšana",
    "Monthly close": "Mēneša noslēgšana",
    # nav sub-items reachable from the Invoicing menu
    "Invoices": "Rēķini",
    "Customer book": "Klientu reģistrs",
    "Issuer profile": "Izrakstītāja profils",

    # ============================================================ COMMON BUTTONS / UI
    "Save": "Saglabāt",
    "Save changes": "Saglabāt izmaiņas",
    "Cancel": "Atcelt",
    "Add": "Pievienot",
    "Delete": "Dzēst",
    "Remove": "Noņemt",
    "Edit": "Rediģēt",
    "Download": "Lejupielādēt",
    "Confirm": "Apstiprināt",
    "Filter": "Filtrēt",
    "Back": "Atpakaļ",
    "Status": "Statuss",
    "Year": "Gads",
    "Number": "Numurs",
    "Customer": "Klients",
    "Country": "Valsts",
    "Email": "E-pasts",
    "Name": "Nosaukums",
    "Notes": "Piezīmes",
    "Currency": "Valūta",
    "Description": "Apraksts",
    "Quantity": "Daudzums",
    "Unit": "Vienība",

    # status chips reachable from the invoicing pages
    "draft": "melnraksts",
    "issued": "izrakstīts",

    # ============================================================ INVOICING PAGES (/invoicing*)
    "Issue date": "Izrakstīšanas datums",
    "Due": "Termiņš",
    "Due date": "Apmaksas termiņš",
    "Total (gross)": "Kopā (ar PVN)",
    "PDF": "PDF",
    "No invoices yet": "Vēl nav rēķinu",
    "Compose your first invoice to a customer.": "Sastādiet savu pirmo rēķinu klientam.",
    "New invoice": "Jauns rēķins",
    "Issue legally-compliant sales invoices to your own "
    "customers. Amounts are shown on a NET basis (VAT excluded). A "
    "draft is freely editable; once issued it gets a gap-free number "
    "and becomes immutable (a legal record).":
        "Izrakstiet juridiski atbilstošus pārdošanas rēķinus saviem klientiem. "
        "Summas tiek rādītas NETO (bez PVN). Melnrakstu var brīvi rediģēt; pēc "
        "izrakstīšanas tas saņem nepārtrauktas numerācijas numuru un kļūst "
        "nemainīgs (juridisks ieraksts).",

    # customer book
    "VAT no": "PVN nr.",
    "No customers yet — add one below.": "Vēl nav klientu — pievienojiet to zemāk.",
    "Edit customer": "Rediģēt klientu",
    "Add a customer": "Pievienot klientu",
    "Add customer": "Pievienot klientu",
    "Country (ISO-2)": "Valsts (ISO-2)",
    "VAT number": "PVN numurs",
    "Reg number": "Reģistrācijas numurs",
    "Address": "Adrese",
    "Payment terms (days)": "Apmaksas termiņš (dienas)",
    "These are your customers (bill-to). They are kept separate "
    "from the platform CRM (which holds the VAT-refund clients).":
        "Šie ir jūsu klienti (maksātāji). Tie tiek glabāti atsevišķi no "
        "platformas CRM (kurā ir PVN atgūšanas klienti).",
    "Customer saved.": "Klients saglabāts.",
    "Back to customer book": "Atpakaļ uz klientu reģistru",

    # issuer profile
    "Legal name": "Juridiskais nosaukums",
    "IBAN": "IBAN",
    "Bank": "Banka",
    "Number series": "Numuru sērija",
    "Number format": "Numura formāts",
    "Default payment terms (days)": "Noklusētais apmaksas termiņš (dienas)",
    "Header text (optional)": "Galvenes teksts (neobligāti)",
    "Save issuer profile": "Saglabāt izrakstītāja profilu",

    # compose / view
    "Reverse charge": "Apgrieztā PVN maksāšana",
    "Net total": "Neto kopā",
    "VAT total": "PVN kopā",
    "Grand total": "Kopā apmaksai",
    "Download PDF": "Lejupielādēt PDF",
    "Lines (net basis, VAT excluded)": "Pozīcijas (neto, bez PVN)",
    "No lines yet.": "Vēl nav pozīciju.",
    "Add a line": "Pievienot pozīciju",
    "Add line": "Pievienot pozīciju",
    "Unit price (net)": "Vienības cena (neto)",
    "Rate": "Likme",
    "Net": "Neto",
    "VAT": "PVN",
    "Settings": "Iestatījumi",
    "Update settings": "Atjaunot iestatījumus",
    "Supply date": "Piegādes datums",
    "Issue": "Izrakstīt",
    "Issue invoice": "Izrakstīt rēķinu",
    "Start draft": "Sākt melnrakstu",
    "Start a draft": "Sākt melnrakstu",
    "Date of supply": "Piegādes datums",

    # compose overhaul — clean layout, live line editor, inline customer
    "Line items": "Pozīcijas",
    "Save draft": "Saglabāt melnrakstu",
    "Saving…": "Saglabā…",
    "More options": "Vairāk iestatījumu",
    "+ Add line": "+ Pievienot pozīciju",
    "preview — the server recomputes the final figures on Save/Issue":
        "priekšskatījums — galīgās summas pārrēķina serveris, saglabājot/izrakstot",
    "Not ready to issue": "Nav gatavs izrakstīšanai",
    "(assigned at issue)": "(piešķir izrakstot)",
    "yes — recipient accounts for VAT": "jā — PVN uzskaita saņēmējs",
    "no": "nē",
    "Custom…": "Pielāgota…",
    "Custom rate %": "Pielāgota likme %",
    "(only if Custom)": "(tikai ja Pielāgota)",
    "VAT rate (Latvia 2026)": "PVN likme (Latvija 2026)",
    "FX rate": "Valūtas kurss",
    "Download e-invoice (XML)": "Lejupielādēt e-rēķinu (XML)",
    "Download hybrid PDF (PDF + e-invoice)": "Lejupielādēt hibrīda PDF (PDF + e-rēķins)",
    "— choose a customer —": "— izvēlieties klientu —",
    "+ New customer": "+ Jauns klients",
    "New customer": "Jauns klients",
    "No customers yet — choose “+ New customer” to add one inline.":
        "Vēl nav klientu — izvēlieties “+ Jauns klients”, lai pievienotu uzreiz.",
    "Could not add customer.": "Neizdevās pievienot klientu.",
    "Could not start the draft.": "Neizdevās sākt melnrakstu.",
    "Could not save the lines.": "Neizdevās saglabāt pozīcijas.",
    "Could not issue.": "Neizdevās izrakstīt.",
    "This invoice is issued and immutable. Download the PDF above.":
        "Šis rēķins ir izrakstīts un nemaināms. Lejupielādējiet PDF augstāk.",

    # ============================================================ INVOICE TEMPLATE (PDF/HTML)
    # The labels that print on the document itself (driven by the invoice language).
    "INVOICE": "RĒĶINS",
    "Invoice": "Rēķins",
    "Bill to": "Maksātājs",
    "Reg. no": "Reģ. nr.",
    "Qty": "Daudz.",
    "Unit price": "Vienības cena",
    "Total net": "Kopā neto",
    "Total VAT": "Kopā PVN",
    "VAT in EUR": "PVN EUR",
    "VAT rate": "PVN likme",
    "Taxable net": "Ar nodokli apliekamā neto",
    "VAT amount": "PVN summa",
    "Payment due": "Apmaksas termiņš",
    "DRAFT": "MELNRAKSTS",

    # ============================================================ LEGAL / VAT TERMINOLOGY
    # ⚠️ NATIVE-SPEAKER / PROFESSIONAL REVIEW REQUIRED before issuing real invoices. ⚠️
    "Reverse charge — VAT to be accounted for by the recipient "
    "(Art. 196 Directive 2006/112/EC).":
        "Apgrieztā PVN maksāšana — PVN aprēķina un maksā saņēmējs "
        "(Direktīvas 2006/112/EK 196. pants).",
    "Simplified invoice": "Vienkāršots rēķins",
    "Amounts are NET (VAT excluded) unless stated.":
        "Summas ir NETO (bez PVN), ja nav norādīts citādi.",

    # ============================================================ PHASE 3: PAYMENTS / AR
    # Payment / status tracking, the AR/aging view and the bank-statement import flow.
    # ⚠️ accounting wording — native-speaker review recommended before customer use. ⚠️
    # statuses (DISPLAY)
    "partially_paid": "daļēji apmaksāts",
    "paid": "apmaksāts",
    "overdue": "nokavēts",
    "sent": "nosūtīts",
    "cancelled": "atcelts",
    # payment ledger + form (compose page)
    "Payments": "Maksājumi",
    "Record payment": "Reģistrēt maksājumu",
    "Paid to date": "Apmaksāts līdz šim",
    "Outstanding": "Neapmaksāts atlikums",
    "No payments recorded yet.": "Vēl nav reģistrētu maksājumu.",
    "This invoice is fully paid.": "Šis rēķins ir pilnībā apmaksāts.",
    "Amount": "Summa",
    "Date": "Datums",
    "Method": "Metode",
    "Reference": "Atsauce",
    "Source": "Avots",
    "manual": "manuāls",
    "bank": "banka",
    "The amount is prefilled to the outstanding balance. Recording a payment "
    "updates the invoice status (partially paid / paid).":
        "Summa ir aizpildīta ar neapmaksāto atlikumu. Maksājuma reģistrēšana "
        "atjaunina rēķina statusu (daļēji apmaksāts / apmaksāts).",
    # AR / aging view
    "Accounts receivable": "Debitoru parādi",
    "Total outstanding": "Kopā neapmaksāts",
    "Outstanding invoices": "Neapmaksātie rēķini",
    "Aging bucket": "Novecošanas grupa",
    "Invoices": "Rēķini",
    "Current (not due)": "Tekošie (vēl nav termiņa)",
    "1–30 days": "1–30 dienas",
    "31–60 days": "31–60 dienas",
    "60+ days": "60+ dienas",
    "Days past due": "Nokavētās dienas",
    "Nothing outstanding — every issued invoice is paid.":
        "Nav neapmaksātu — visi izrakstītie rēķini ir apmaksāti.",
    "Outstanding = invoice gross − payments recorded. Overdue is derived "
    "from the due date + the paid total, so it is always current.":
        "Neapmaksāts = rēķina kopsumma ar PVN − reģistrētie maksājumi. Nokavējums "
        "tiek atvasināts no apmaksas termiņa un apmaksātās summas, tāpēc tas vienmēr "
        "ir aktuāls.",
    # bank-statement import + advisory matching
    "Import bank statement": "Importēt bankas izrakstu",
    "Statement file": "Izraksta fails",
    "Upload and match": "Augšupielādēt un sasaistīt",
    "Upload a bank statement (ISO 20022 camt.053 XML or a CSV export). "
    "Each incoming credit is matched — advisory only — to an open "
    "invoice by invoice number in the reference, then exact amount, "
    "then payer IBAN. You confirm each match before any payment is "
    "recorded.":
        "Augšupielādējiet bankas izrakstu (ISO 20022 camt.053 XML vai CSV eksportu). "
        "Katrs ienākošais kredīts tiek sasaistīts — tikai informatīvi — ar atvērtu "
        "rēķinu pēc rēķina numura atsaucē, pēc tam pēc precīzas summas un pēc maksātāja "
        "IBAN. Pirms maksājuma reģistrēšanas jūs apstiprināt katru sasaisti.",
    "No file uploaded.": "Nav augšupielādēts fails.",
    "That statement is too large.": "Šis izraksts ir pārāk liels.",
    "No incoming credits found in that statement "
    "(is it a camt.053 or a recognised CSV?).":
        "Šajā izrakstā nav atrasti ienākošie kredīti "
        "(vai tas ir camt.053 vai atpazīts CSV?).",
    "Review matches": "Pārskatīt sasaistes",
    "Advisory only. Uncheck any row you do not want to book. Confirming "
    "records a payment (source: bank) against each accepted invoice; "
    "re-importing the same statement will not double-record.":
        "Tikai informatīvi. Noņemiet atzīmi rindām, kuras nevēlaties grāmatot. "
        "Apstiprināšana reģistrē maksājumu (avots: banka) katram pieņemtajam rēķinam; "
        "tā paša izraksta atkārtota importēšana neradīs dubultu ierakstu.",
    "Accept": "Pieņemt",
    "Payer": "Maksātājs",
    "Suggested invoice": "Ieteiktais rēķins",
    "Matched by": "Sasaistīts pēc",
    "no match": "nav atbilstības",
    "number": "numurs",
    "amount": "summa",
    "iban": "iban",
    "Confirm accepted matches": "Apstiprināt pieņemtās sasaistes",
    "for bank-statement matching": "bankas izraksta sasaistei",
    "Recorded %d payment(s); %d skipped.": "Reģistrēti %d maksājumi; %d izlaisti.",

    # ============================================================ PHASE 4: EMAIL + CREDIT NOTES
    # Emailing the invoice to the customer, and credit notes / cancellation (kreditrēķins).
    # ⚠️ accounting/legal wording — native-speaker review recommended before customer use. ⚠️
    # --- email the invoice ---
    "Send to customer": "Nosūtīt klientam",
    "Send invoice by email": "Nosūtīt rēķinu pa e-pastu",
    "Send the invoice (PDF + e-invoice XML) to the customer by email.":
        "Nosūtīt rēķinu (PDF + e-rēķina XML) klientam pa e-pastu.",
    "Recipient email": "Saņēmēja e-pasts",
    "Leave blank to use the customer's stored email.":
        "Atstājiet tukšu, lai izmantotu klienta saglabāto e-pastu.",
    "Send": "Nosūtīt",
    "Sent": "Nosūtīts",
    "Sent to": "Nosūtīts uz",
    "Not sent yet.": "Vēl nav nosūtīts.",
    "The invoice was emailed to the customer.": "Rēķins ir nosūtīts klientam pa e-pastu.",
    "Email is not configured (set up the SMTP relay in Admin).":
        "E-pasts nav konfigurēts (iestatiet SMTP releju sadaļā Administrēšana).",
    "The customer has no email address.": "Klientam nav e-pasta adreses.",
    "Please find the invoice attached (PDF and e-invoice XML).":
        "Pielikumā pievienots rēķins (PDF un e-rēķina XML).",
    "Invoice %s": "Rēķins %s",
    "Credit note %s": "Kreditrēķins %s",
    # --- credit note / cancellation ---
    "CREDIT NOTE": "KREDITRĒĶINS",
    "Credit note": "Kreditrēķins",
    "Credit / cancel": "Kreditēt / atcelt",
    "Credit or cancel invoice": "Kreditēt vai atcelt rēķinu",
    "Full cancellation": "Pilna atcelšana",
    "Partial credit": "Daļēja kreditēšana",
    "Mode": "Režīms",
    "Qty to credit": "Kreditējamais daudzums",
    "Could not send the invoice.": "Neizdevās nosūtīt rēķinu.",
    "Could not create the credit note.": "Neizdevās izveidot kreditrēķinu.",
    "Reason": "Iemesls",
    "Original invoice": "Sākotnējais rēķins",
    "References original invoice": "Atsaucas uz sākotnējo rēķinu",
    "the amounts are a credit to the customer": "summas ir kredīts klientam",
    "Credited": "Kreditēts",
    "credited": "kreditēts",
    "Amount to credit": "Kreditējamā summa",
    "Create credit note": "Izveidot kreditrēķinu",
    "A credit note is the legal way to reverse or correct an issued invoice "
    "(the original stays immutable). A full cancellation mirrors every line; a "
    "partial credit lets you choose the amounts to credit. The credit note is a "
    "draft until you issue it.":
        "Kreditrēķins ir likumīgais veids, kā atcelt vai labot izrakstītu rēķinu "
        "(sākotnējais paliek nemainīgs). Pilna atcelšana atspoguļo katru rindu; "
        "daļēja kreditēšana ļauj izvēlēties kreditējamās summas. Kreditrēķins ir "
        "melnraksts, līdz to izrakstāt.",
    "This credit note references the original invoice (number + issue date) and "
    "the reason for the credit. Amounts are shown as a credit.":
        "Šis kreditrēķins atsaucas uz sākotnējo rēķinu (numurs + datums) un "
        "kreditēšanas iemeslu. Summas ir norādītas kā kredīts.",
    "Only an issued invoice can be credited.": "Kreditēt var tikai izrakstītu rēķinu.",
    "The amount to credit exceeds the original.":
        "Kreditējamā summa pārsniedz sākotnējo rēķinu.",
    "Choose at least one line to credit.": "Izvēlieties vismaz vienu rindu kreditēšanai.",

    # ============================================================ PHASE 5: REPORTS SUITE
    # The read-only invoicing reporting suite: VAT output (PVN), revenue, customer
    # statements, AR aging. ⚠️ accounting/legal/VAT-return wording — native-speaker +
    # accountant review recommended before the figures are filed/sent to a customer. ⚠️
    # --- nav / shared ---
    "Invoicing reports": "Rēķinu pārskati",
    "Read-only reports over your issued invoices, lines, payments and "
    "credit notes. Amounts are EUR; revenue and output VAT are on a NET "
    "(VAT-excluded) basis (a gross column is labelled where shown).":
        "Tikai lasāmi pārskati par jūsu izrakstītajiem rēķiniem, rindām, maksājumiem un "
        "kreditrēķiniem. Summas ir EUR; ieņēmumi un aprēķinātais PVN ir uz NETO "
        "(bez PVN) bāzes (ar PVN aile ir atzīmēta, kur tāda ir).",
    "Period type": "Perioda veids",
    "Month": "Mēnesis",
    "Quarter": "Ceturksnis",
    "Show": "Rādīt",
    "From": "No",
    "To": "Līdz",
    "Excel": "Excel",
    "No data for this period.": "Šajā periodā nav datu.",
    "Could not render the PDF.": "Neizdevās izveidot PDF.",
    # --- VAT output report (PVN) ---
    "Output VAT report (PVN)": "Aprēķinātā PVN pārskats (PVN)",
    "Output VAT": "Aprēķinātais PVN",
    "Total output VAT": "Kopā aprēķinātais PVN",
    "Reverse charge (AE)": "Apgrieztā PVN maksāšana (AE)",
    "0% / exempt": "0% / atbrīvots",
    "Tax point = issue date; drafts excluded; credit notes reduce output "
    "VAT. Reverse-charge and 0%/exempt supplies carry no output VAT but are "
    "reportable. EUR, NET (taxable) basis.":
        "Nodokļa rašanās brīdis = izrakstīšanas datums; melnraksti netiek iekļauti; "
        "kreditrēķini samazina aprēķināto PVN. Apgrieztā PVN maksāšanas un 0%/atbrīvotās "
        "piegādes nerada aprēķināto PVN, bet ir jāatspoguļo. EUR, NETO (ar nodokli "
        "apliekamā) bāze.",
    "Reverse-charge and 0%/exempt supplies are reportable but carry no "
    "output VAT.":
        "Apgrieztā PVN maksāšanas un 0%/atbrīvotās piegādes ir jāatspoguļo, bet tās "
        "nerada aprēķināto PVN.",
    # --- revenue report ---
    "Sales / revenue report": "Pārdošanas / ieņēmumu pārskats",
    "Revenue by month": "Ieņēmumi pa mēnešiem",
    "Revenue by customer": "Ieņēmumi pa klientiem",
    "Revenue by service": "Ieņēmumi pa pakalpojumiem",
    "Credit notes": "Kreditrēķini",
    "Service / description": "Pakalpojums / apraksts",
    "Lines": "Rindas",
    "Total": "Kopā",
    "NET (VAT-excluded) EUR; tax point = issue date; drafts excluded; "
    "credit notes subtracted.":
        "NETO (bez PVN) EUR; nodokļa rašanās brīdis = izrakstīšanas datums; melnraksti "
        "netiek iekļauti; kreditrēķini atskaitīti.",
    # --- customer statement ---
    "Statement of account": "Norēķinu izraksts",
    "Choose a customer and a date range.": "Izvēlieties klientu un datumu diapazonu.",
    "No entries in this date range.": "Šajā datumu diapazonā nav ierakstu.",
    "Opening balance": "Sākuma atlikums",
    "Closing balance": "Beigu atlikums",
    "Debit": "Debets",
    "Credit": "Kredīts",
    "Balance": "Atlikums",
    "Type": "Veids",
    "invoice": "rēķins",
    "credit_note": "kreditrēķins",
    "payment": "maksājums",
    "Could not build the statement.": "Neizdevās izveidot izrakstu.",
    "Email statement to customer": "Nosūtīt izrakstu klientam pa e-pastu",
    "Please find your statement of account attached.":
        "Pielikumā pievienots jūsu norēķinu izraksts.",
    "The statement was emailed to the customer.":
        "Izraksts ir nosūtīts klientam pa e-pastu.",
    "Could not send the statement.": "Neizdevās nosūtīt izrakstu.",
    "GROSS EUR. Debit = invoice; credit = credit note / payment.":
        "Ar PVN EUR. Debets = rēķins; kredīts = kreditrēķins / maksājums.",
    # --- AR aging report ---
    "Accounts receivable aging": "Debitoru parādu novecošana",
    "Overdue": "Nokavēts",
    "As of": "Uz",
    "Outstanding EUR (gross − payments − credits). Overdue = past-due "
    "buckets (1-30 + 31-60 + 60+). As of today.":
        "Neapmaksāts EUR (ar PVN − maksājumi − kredīti). Nokavēts = nokavētās grupas "
        "(1-30 + 31-60 + 60+). Uz šodienu.",

    # ============================================================ PHASE 6: RECURRING INVOICES
    # A template that auto-generates invoices on a schedule (e.g. monthly fuel-card billing).
    # ⚠️ accounting wording — native-speaker review recommended before live use. ⚠️
    "Recurring invoices": "Periodiskie rēķini",
    "Define a template that auto-generates invoices on a schedule (e.g. "
    "monthly fuel-card billing). Amounts are on a NET basis (VAT excluded).":
        "Definējiet veidni, kas automātiski izveido rēķinus pēc grafika (piemēram, "
        "ikmēneša degvielas karšu rēķini). Summas ir uz NETO (bez PVN) bāzes.",
    "Recurring template saved.": "Periodiskā veidne saglabāta.",
    "New recurring template": "Jauna periodiskā veidne",
    "Edit recurring template": "Rediģēt periodisko veidni",
    "Create template": "Izveidot veidni",
    "No recurring templates yet — create one below.":
        "Vēl nav periodisko veidņu — izveidojiet kādu zemāk.",
    "Back to recurring invoices": "Atpakaļ uz periodiskajiem rēķiniem",
    "Templates": "Veidnes",
    "Frequency": "Biežums",
    "Every N periods": "Ik pēc N periodiem",
    "Next run": "Nākamā izpilde",
    "Auto-issue": "Automātiski izrakstīt",
    "Generated": "Izveidoti",
    "Generated invoices": "Izveidotie rēķini",
    "No invoices generated yet.": "Vēl nav izveidots neviens rēķins.",
    "Start date": "Sākuma datums",
    "End date (optional)": "Beigu datums (neobligāts)",
    "Max occurrences (optional)": "Maks. reižu skaits (neobligāts)",
    "e.g. Monthly fuel-card billing": "piemēram, ikmēneša degvielas karšu rēķini",
    "weekly": "iknedēļas",
    "monthly": "ikmēneša",
    "quarterly": "ceturkšņa",
    "yearly": "gada",
    "active": "aktīvs",
    "paused": "pauzēts",
    "Pause": "Pauzēt",
    "Resume": "Atsākt",
    "Template paused.": "Veidne pauzēta.",
    "Template resumed.": "Veidne atsākta.",
    "Template deleted.": "Veidne dzēsta.",
    "No template selected.": "Nav izvēlēta neviena veidne.",
    "Scheduler": "Plānotājs",
    "Auto-generate due recurring invoices daily":
        "Automātiski izveidot pienākušos periodiskos rēķinus katru dienu",
    "Save scheduler state": "Saglabāt plānotāja stāvokli",
    "Last run": "Pēdējā izpilde",
    "Generate due now": "Izveidot pienākušos tagad",
    "Templates due:": "Pienākušās veidnes:",
    "Recurring scheduler is ON (runs on the worker tier).":
        "Periodiskais plānotājs ir IESLĒGTS (darbojas darbinieku slānī).",
    "Recurring scheduler is OFF.": "Periodiskais plānotājs ir IZSLĒGTS.",
    "Could not generate due invoices.": "Neizdevās izveidot pienākušos rēķinus.",
    "Yes": "Jā",
    "No": "Nē",
    "Auto-issue OFF (the default) generates a DRAFT for review at each due "
    "date; turn it ON to assign the gap-free number automatically. The "
    "next-run date advances month-end safely (a 31st lands on the last day "
    "of a short month).":
        "Automātiskā izrakstīšana IZSLĒGTA (noklusējums) katrā termiņā izveido "
        "MELNRAKSTU pārbaudei; ieslēdziet to, lai automātiski piešķirtu secīgo numuru. "
        "Nākamais datums tiek pārcelts droši attiecībā uz mēneša beigām (31. datums "
        "nonāk īsa mēneša pēdējā dienā).",
    "The scheduler is OFF by default and runs on the worker tier (one leader "
    "across processes) — enable a worker to actually run it. 'Generate due "
    "now' works without the scheduler. Generation is idempotent: a template "
    "generates at most one invoice per due date.":
        "Plānotājs pēc noklusējuma ir IZSLĒGTS un darbojas darbinieku slānī (viens "
        "līderis starp procesiem) — iespējojiet darbinieku, lai tas patiešām darbotos. "
        "“Izveidot pienākušos tagad” darbojas bez plānotāja. Ģenerēšana ir idempotenta: "
        "viena veidne katrā termiņā izveido ne vairāk kā vienu rēķinu.",
    # PHASE 7 — logo / branding, discounts, proforma / quote ----------------------
    "PROFORMA INVOICE": "Priekšapmaksas rēķins",
    "Proforma invoice": "Priekšapmaksas rēķins",
    "Proforma series": "Priekšapmaksas sērija",
    "QUOTE": "Piedāvājums",
    "Quote": "Piedāvājums",
    "Quote series": "Piedāvājuma sērija",
    "Credit-note series": "Kreditrēķina sērija",
    "Document type": "Dokumenta tips",
    "not a VAT invoice, not a demand for payment":
        "nav PVN rēķins, nav maksājuma pieprasījums",
    "not a VAT invoice": "nav PVN rēķins",
    "This is a quote — not a demand for payment.":
        "Šis ir piedāvājums — nav maksājuma pieprasījums.",
    "Proforma — for advance payment / order confirmation; not a VAT invoice.":
        "Priekšapmaksas rēķins — avansa maksājumam / pasūtījuma apstiprinājumam; nav PVN rēķins.",
    "Discount": "Atlaide",
    "Line discount": "Rindas atlaide",
    "Document discount": "Dokumenta atlaide",
    "Discount value": "Atlaides vērtība",
    "Subtotal (net)": "Starpsumma (neto)",
    "none": "nav",
    "percent (%)": "procenti (%)",
    "amount (EUR)": "summa (EUR)",
    "Apply": "Piemērot",
    "Convert to invoice": "Pārvērst par rēķinu",
    "Could not convert to invoice.": "Neizdevās pārvērst par rēķinu.",
    "Creates a real DRAFT invoice copying the customer, lines and discounts; it then "
    "issues normally and gets the legal invoice number.":
        "Izveido īstu rēķina MELNRAKSTU, kopējot klientu, rindas un atlaides; pēc tam to "
        "izraksta parastā kārtībā un piešķir likumīgo rēķina numuru.",
    "Brand colour": "Zīmola krāsa",
    "Logo image (PNG/JPG, max 512 KiB)": "Logotipa attēls (PNG/JPG, maks. 512 KiB)",
    "Remove logo": "Noņemt logotipu",
    "A logo is set": "Logotips ir iestatīts",
    "No logo set.": "Logotips nav iestatīts.",
    "Logo saved.": "Logotips saglabāts.",
    "Logo removed.": "Logotips noņemts.",
    "Could not save the logo.": "Neizdevās saglabāt logotipu.",
}
