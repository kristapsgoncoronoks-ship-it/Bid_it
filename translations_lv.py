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
}
