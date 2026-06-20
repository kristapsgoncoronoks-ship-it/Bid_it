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
}
