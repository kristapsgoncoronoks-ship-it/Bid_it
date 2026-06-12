import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

WORKDIR = os.path.dirname(os.path.abspath(__file__))

# (invoice, card, date, time, product, station, price, qty, vat%, amount, card_total_stated)
T = []
def add(card, rows, card_total, inv="BEOI00118939/DE", vat=21.0):
    for r in rows:
        T.append((inv, card, *r, vat, card_total))

add("11525-0 042", [("18/05/26","06:30","DIESEL","Zeebrugge",1.7488,400.42,700.25),
                    ("20/05/26","15:54","DIESEL","Zeebrugge",1.8116,430.00,778.99)], 1479.24)
add("11541-0 044", [("25/05/26","13:36","DIESEL","Zeebrugge",1.7694,627.36,1110.05),
                    ("27/05/26","17:22","DIESEL","Zeebrugge",1.7694,470.06,831.72)], 1941.77)
add("11558-0 045", [("20/05/26","13:05","DIESEL","Waregem",1.8116,830.04,1503.70),
                    ("27/05/26","09:00","DIESEL","Waregem",1.7694,510.00,902.39)], 2406.09)
add("11657-0 010", [("20/05/26","13:45","DIESEL","Zeebrugge",1.8116,340.00,615.94)], 615.94)
add("11665-0 011", [("20/05/26","09:21","DIESEL","Zeebrugge",1.8116,800.16,1449.57)], 1449.57)
add("11673-0 012", [("30/05/26","17:49","DIESEL","Tournai",1.6843,222.63,374.98)], 374.98)
add("11715-0 016", [("20/05/26","11:28","AdBlue","Zeebrugge",1.3120,50.56,66.33),
                    ("20/05/26","11:21","DIESEL","Zeebrugge",1.8116,443.50,803.44),
                    ("27/05/26","13:02","DIESEL","Zeebrugge",1.7694,309.80,548.16)], 1417.93)
add("11731-0 018", [("19/05/26","16:18","AdBlue","Zeebrugge",1.3120,20.09,26.36),
                    ("19/05/26","16:09","DIESEL","Zeebrugge",1.7488,600.03,1049.33)], 1075.69)
add("11756-0 020", [("26/05/26","16:32","AdBlue","Bierset_Liège Air",1.3120,45.14,59.22),
                    ("26/05/26","16:22","DIESEL","Bierset_Liège Air",1.7694,396.64,701.81)], 761.03)
add("11764-0 021", [("29/05/26","14:12","AdBlue","Zeebrugge",1.3120,16.00,20.99),
                    ("23/05/26","17:53","DIESEL","Tournai",1.7694,456.00,806.85),
                    ("29/05/26","14:15","DIESEL","Zeebrugge",1.6843,471.00,793.31)], 1621.15)
add("11772-0 022", [("27/05/26","17:28","DIESEL","Zeebrugge",1.7694,200.17,354.18),
                    ("30/05/26","12:43","DIESEL","Zeebrugge",1.6843,385.18,648.76),
                    ("30/05/26","12:50","DIESEL","Zeebrugge",1.6843,215.24,362.53)], 1365.47)
add("11780-0 023", [("18/05/26","06:59","DIESEL","Zeebrugge",1.7488,300.01,524.66)], 524.66)
add("11806-0 024", [("30/05/26","14:12","AdBlue","Zeebrugge",1.3120,20.19,26.49),
                    ("19/05/26","10:00","DIESEL","Zeebrugge",1.7488,421.09,736.40),
                    ("27/05/26","17:22","DIESEL","Waregem",1.7694,490.56,868.00),
                    ("30/05/26","13:52","DIESEL","Zeebrugge",1.6843,490.31,825.83)], 2456.72)
add("11814-0 025", [("21/05/26","18:46","DIESEL","Zeebrugge",1.8116,470.01,851.47),
                    ("30/05/26","18:34","DIESEL","Zeebrugge",1.6843,420.01,707.42)], 1558.89)
add("11822-0 026", [("20/05/26","07:50","AdBlue","Zeebrugge",1.3120,3.01,3.95),
                    ("20/05/26","07:55","AdBlue","Zeebrugge",1.3120,42.00,55.10),
                    ("16/05/26","04:49","DIESEL","Zeebrugge",1.7488,330.01,577.12),
                    ("20/05/26","07:41","DIESEL","Zeebrugge",1.8116,530.05,960.24)], 1596.41)
add("11830-0 027", [("21/05/26","12:23","DIESEL","Zeebrugge",1.8116,886.00,1605.08),
                    ("28/05/26","13:42","DIESEL","Zeebrugge",1.7694,690.02,1220.92)], 2826.00)
add("11863-0 032", [("22/05/26","10:24","AdBlue","Zeebrugge",1.3120,30.20,39.62),
                    ("18/05/26","15:44","DIESEL","Zeebrugge",1.7488,580.00,1014.30),
                    ("22/05/26","10:14","DIESEL","Zeebrugge",1.7694,670.00,1185.50),
                    ("30/05/26","16:00","DIESEL","Zeebrugge",1.6843,860.02,1448.53)], 3687.95)
add("11871-0 033", [("18/05/26","10:00","DIESEL","Zeebrugge",1.7488,473.87,828.70),
                    ("22/05/26","12:41","DIESEL","Zeebrugge",1.7694,617.33,1092.30)], 1921.00)
add("11889-0 034", [("21/05/26","09:45","AdBlue","Tournai",1.3120,51.19,67.16),
                    ("21/05/26","09:12","DIESEL","Tournai",1.8116,526.16,953.19),
                    ("21/05/26","09:24","DIESEL","Tournai",1.8116,315.10,570.84)], 1591.19)
add("11897-0 035", [("30/05/26","12:58","DIESEL","Zeebrugge",1.6843,810.01,1364.30)], 1364.30)
add("11947-0 038", [("20/05/26","12:57","DIESEL","Zelzate",1.8116,250.11,453.10)], 453.10)
add("11954-0 039", [("21/05/26","21:19","DIESEL","Zeebrugge",1.8116,545.00,987.32),
                    ("30/05/26","13:38","DIESEL","Zeebrugge",1.6843,730.00,1229.54)], 2216.86)
add("11962-0 040", [("25/05/26","16:38","AdBlue","Zeebrugge",1.3120,22.77,29.87),
                    ("22/05/26","10:38","DIESEL","Zeebrugge",1.7694,202.37,358.07),
                    ("25/05/26","16:29","DIESEL","Zeebrugge",1.7694,234.04,414.11)], 802.05)
add("12127-0 049", [("19/05/26","17:28","DIESEL","Liège Herstal",1.7488,333.24,582.77),
                    ("22/05/26","19:42","DIESEL","Zelzate",1.7694,560.00,990.86),
                    ("28/05/26","08:36","DIESEL","Zeebrugge",1.7694,494.00,874.08)], 2447.71)
# German invoice DEVR00473179, 19% VAT
add("11780-0 023", [("20/05/26","08:36","AdBlue","Lübeck",1.2842,42.47,54.54),
                    ("28/05/26","19:40","AdBlue","Lübeck",1.2842,64.00,82.19),
                    ("20/05/26","08:31","DIESEL","Lübeck",1.7403,258.00,449.00),
                    ("22/05/26","12:41","DIESEL","Lübeck",1.7067,300.05,512.10),
                    ("28/05/26","19:31","DIESEL","Lübeck",1.6529,550.02,909.13)], 2006.96,
    inv="DEVR00473179", vat=19.0)
add("12036-0 051", [("19/05/26","16:58","AdBlue","Lübeck",1.2842,15.01,19.28),
                    ("28/05/26","18:00","AdBlue","Lübeck",1.2842,28.03,36.00),
                    ("19/05/26","16:54","DIESEL","Lübeck",1.7403,252.90,440.12),
                    ("22/05/26","14:07","DIESEL","Lübeck",1.7067,326.95,558.01),
                    ("22/05/26","14:10","DIESEL","Lübeck",1.7067,243.56,415.68),
                    ("28/05/26","17:56","DIESEL","Lübeck",1.6529,292.46,483.41),
                    ("30/05/26","09:40","DIESEL","Lübeck",1.6513,201.88,333.36)], 2285.86,
    inv="DEVR00473179", vat=19.0)

wb = Workbook()
ws = wb.active
ws.title = "Transactions"
hdr = ["Invoice","Card","Date","Time","Product","Station","Net price EUR/L","Quantity L",
       "VAT %","Amount EUR (invoice)","Calc: Price x Qty","Diff EUR","Check"]
ws.append(hdr)
bold = Font(bold=True, name="Arial", size=10)
norm = Font(name="Arial", size=10)
fill = PatternFill("solid", start_color="1F4E78")
for c in ws[1]:
    c.font = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    c.fill = fill
    c.alignment = Alignment(horizontal="center", wrap_text=True)

r = 2
for inv, card, d, t, prod, stn, price, qty, amt, vat, ctot in T:
    ws.append([inv, card, d, t, prod, stn, price, qty, vat, amt,
               f"=ROUND(G{r}*H{r},2)", f"=K{r}-J{r}",
               f'=IF(ABS(L{r})<=0.01,"OK","CHECK")'])
    r += 1
last = r - 1

for row in ws.iter_rows(min_row=2, max_row=last):
    for c in row:
        c.font = norm
for col, w in zip("ABCDEFGHIJKLM", [17,13,10,7,9,16,13,11,7,15,14,9,8]):
    ws.column_dimensions[col].width = w
for row in ws.iter_rows(min_row=2, max_row=last, min_col=7, max_col=8):
    row[0].number_format = "0.0000"
    row[1].number_format = "#,##0.00"
for col in ("J","K","L"):
    for row in ws.iter_rows(min_row=2, max_row=last, min_col=ord(col)-64, max_col=ord(col)-64):
        row[0].number_format = "#,##0.00"
ws.freeze_panes = "A2"
ws.auto_filter.ref = f"A1:M{last}"

# --- Card totals control sheet ---
ws2 = wb.create_sheet("Card totals check")
ws2.append(["Invoice","Card","Stated card total EUR","Calc: sum of transactions","Diff EUR","Check"])
for c in ws2[1]:
    c.font = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    c.fill = fill
    c.alignment = Alignment(horizontal="center", wrap_text=True)
seen = []
for inv, card, ctot_ in [(t[0], t[1], t[-1]) for t in T]:
    key = (inv, card, ctot_)
    if key not in seen:
        seen.append(key)
r2 = 2
for inv, card, ctot in seen:
    f = (f'=ROUND(SUMPRODUCT((Transactions!$A$2:$A${last+0}=A{r2})*'
         f'(Transactions!$B$2:$B${last+0}=B{r2})*Transactions!$J$2:$J${last+0}),2)')
    ws2.append([inv, card, ctot, f, f"=D{r2}-C{r2}", f'=IF(ABS(E{r2})<=0.01,"OK","CHECK")'])
    r2 += 1
last2 = r2 - 1
for row in ws2.iter_rows(min_row=2, max_row=last2):
    for c in row:
        c.font = norm
for row in ws2.iter_rows(min_row=2, max_row=last2, min_col=3, max_col=5):
    for c in row:
        c.number_format = "#,##0.00"
for col, w in zip("ABCDEF", [17,13,19,22,9,8]):
    ws2.column_dimensions[col].width = w

# --- Summary / invoice totals check ---
ws3 = wb.create_sheet("Invoice totals check")
rows3 = [
 ["Check item","Invoice states","Calculated from transactions","Diff","Check"],
 ["BE invoice BEOI00118939/DE — AdBlue litres", 301.15,
  f'=ROUND(SUMPRODUCT((Transactions!$A$2:$A${last}="BEOI00118939/DE")*(Transactions!$E$2:$E${last}="AdBlue")*Transactions!$H$2:$H${last}),2)'],
 ["BE invoice BEOI00118939/DE — DIESEL litres", 21337.55,
  f'=ROUND(SUMPRODUCT((Transactions!$A$2:$A${last}="BEOI00118939/DE")*(Transactions!$E$2:$E${last}="DIESEL")*Transactions!$H$2:$H${last}),2)'],
 ["BE invoice BEOI00118939/DE — Total net EUR", 37955.70,
  f'=ROUND(SUMIF(Transactions!$A$2:$A${last},"BEOI00118939/DE",Transactions!$J$2:$J${last}),2)'],
 ["BE invoice BEOI00118939/DE — VAT 21% EUR", 7970.68, "=ROUND(C4*0.21,2)"],
 ["BE invoice BEOI00118939/DE — Total to pay EUR", 45926.38, "=C4+C5"],
 ["DE invoice DEVR00473179 — AdBlue litres", 149.51,
  f'=ROUND(SUMPRODUCT((Transactions!$A$2:$A${last}="DEVR00473179")*(Transactions!$E$2:$E${last}="AdBlue")*Transactions!$H$2:$H${last}),2)'],
 ["DE invoice DEVR00473179 — DIESEL litres", 2425.82,
  f'=ROUND(SUMPRODUCT((Transactions!$A$2:$A${last}="DEVR00473179")*(Transactions!$E$2:$E${last}="DIESEL")*Transactions!$H$2:$H${last}),2)'],
 ["DE invoice DEVR00473179 — Total net EUR", 4292.82,
  f'=ROUND(SUMIF(Transactions!$A$2:$A${last},"DEVR00473179",Transactions!$J$2:$J${last}),2)'],
 ["DE invoice DEVR00473179 — VAT 19% EUR", 815.63, "=ROUND(C9*0.19,2)"],
 ["DE invoice DEVR00473179 — Total debit EUR", 5108.45, "=C9+C10"],
 ["GRAND TOTAL both invoices EUR", 51034.83, "=C6+C11"],
]
for i, rr in enumerate(rows3, start=1):
    if i == 1:
        ws3.append(rr)
    else:
        ws3.append([rr[0], rr[1], rr[2], f"=C{i}-B{i}", f'=IF(ABS(D{i})<=0.01,"OK","CHECK")'])
for c in ws3[1]:
    c.font = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    c.fill = fill
for row in ws3.iter_rows(min_row=2, max_row=12):
    for c in row:
        c.font = norm
    for c in row[1:4]:
        c.number_format = "#,##0.00"
for col, w in zip("ABCDE", [44,16,26,10,8]):
    ws3.column_dimensions[col].width = w

wb.save(os.path.join(WORKDIR, "Q8_fuel_invoice_DE00752298_transactions.xlsx"))
print("rows:", last-1)
