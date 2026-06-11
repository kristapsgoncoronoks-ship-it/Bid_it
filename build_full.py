from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

# (card, date, time, product, station, country, currency, local_price, fx, eur_price, qty, amount_eur)
T = []
def add(card, ctry, rows, ctot):
    for r in rows:
        T.append((card, ctry, *r, ctot))

# rows: (date, time, product, station, currency, local_price, fx, eur_price, qty, amt)
E = "EUR"
add("11525 042","Belgium",[("18/05/26","06:30","DIESEL","Zeebrugge",E,1.7488,1.0,1.7488,400.42,700.25),
 ("20/05/26","15:54","DIESEL","Zeebrugge",E,1.8116,1.0,1.8116,430.00,778.99)],1479.24)
add("11533 043","France",[("30/05/26","09:16","Gasoil N","Langres_Rolampont",E,1.7922,1.0,1.7922,200.00,358.44)],358.44)
add("11541 044","Spain/Belgium",[
 ("18/05/26","08:39","AdBlue","IRUN ARASO",E,0.9876,1.0,0.9876,45.85,45.28),
 ("21/05/26","12:23","AdBlue","IRUN ARASO",E,0.9876,1.0,0.9876,41.57,41.05),
 ("18/05/26","08:29","GASOIL","IRUN ARASO",E,1.5218,1.0,1.5218,410.81,625.17),
 ("21/05/26","12:10","GASOIL","IRUN ARASO",E,1.5409,1.0,1.5409,369.40,569.21),
 ("25/05/26","13:36","DIESEL","Zeebrugge",E,1.7694,1.0,1.7694,627.36,1110.05),
 ("27/05/26","17:22","DIESEL","Zeebrugge",E,1.7694,1.0,1.7694,470.06,831.72)],3222.48)
add("11558 045","Belgium",[("20/05/26","13:05","DIESEL","Waregem",E,1.8116,1.0,1.8116,830.04,1503.70),
 ("27/05/26","09:00","DIESEL","Waregem",E,1.7694,1.0,1.7694,510.00,902.39)],2406.09)
add("11657 010","Belgium",[("20/05/26","13:45","DIESEL","Zeebrugge",E,1.8116,1.0,1.8116,340.00,615.94)],615.94)
add("11665 011","Belgium/Spain",[("20/05/26","09:21","DIESEL","Zeebrugge",E,1.8116,1.0,1.8116,800.16,1449.57),
 ("26/05/26","17:57","GASOIL","IRUN ARASO",E,1.5145,1.0,1.5145,658.36,997.09)],2446.66)
add("11673 012","Spain/Belgium",[
 ("28/05/26","16:58","AdBlue","IRUN ARASO",E,0.9876,1.0,0.9876,69.83,68.96),
 ("23/05/26","10:12","GASOIL","IRUN ARASO",E,1.5145,1.0,1.5145,478.37,724.49),
 ("28/05/26","16:47","GASOIL","IRUN ARASO",E,1.5145,1.0,1.5145,676.48,1024.53),
 ("30/05/26","17:49","DIESEL","Tournai",E,1.6843,1.0,1.6843,222.63,374.98)],2192.96)
add("11715 016","Belgium/Spain",[
 ("20/05/26","11:28","AdBlue","Zeebrugge",E,1.3120,1.0,1.3120,50.56,66.33),
 ("20/05/26","11:21","DIESEL","Zeebrugge",E,1.8116,1.0,1.8116,443.50,803.44),
 ("22/05/26","06:11","GASOIL","IRUN ARASO",E,1.5409,1.0,1.5409,345.48,532.35),
 ("25/05/26","17:03","GASOIL","IRUN ARASO",E,1.5145,1.0,1.5145,198.66,300.87),
 ("27/05/26","13:02","DIESEL","Zeebrugge",E,1.7694,1.0,1.7694,309.80,548.16)],2251.15)
add("11731 018","Belgium",[("19/05/26","16:18","AdBlue","Zeebrugge",E,1.3120,1.0,1.3120,20.09,26.36),
 ("19/05/26","16:09","DIESEL","Zeebrugge",E,1.7488,1.0,1.7488,600.03,1049.33)],1075.69)
add("11749 019","Denmark",[
 ("29/05/26","08:57","IDS ADBLUE","LÅSBY_KALBYGAARD","DKK",8.1400,7.4731,1.0892,76.00,82.78),
 ("29/05/26","09:04","GOEASY IDS TRUCK DIESEL","LÅSBY_KALBYGAARD","DKK",13.7042,7.4731,1.8338,291.20,534.00),
 ("29/05/26","09:10","GOEASY IDS TRUCK DIESEL","LÅSBY_KALBYGAARD","DKK",13.7042,7.4731,1.8338,475.28,871.57)],1488.35)
add("11756 020","Belgium/Denmark",[
 ("26/05/26","16:32","AdBlue","Bierset_Liège Air",E,1.3120,1.0,1.3120,45.14,59.22),
 ("30/05/26","06:45","IDS ADBLUE","KØGE_E20","DKK",8.1400,7.4731,1.0892,36.01,39.22),
 ("26/05/26","16:22","DIESEL","Bierset_Liège Air",E,1.7694,1.0,1.7694,396.64,701.81),
 ("30/05/26","06:24","GOEASY IDS TRUCK DIESEL","KØGE_E20","DKK",13.6242,7.4731,1.8231,460.05,838.72),
 ("30/05/26","06:36","GOEASY IDS TRUCK DIESEL","KØGE_E20","DKK",13.6242,7.4731,1.8231,394.90,719.94)],2358.91)
add("11764 021","Belgium/Spain",[
 ("29/05/26","14:12","AdBlue","Zeebrugge",E,1.3120,1.0,1.3120,16.00,20.99),
 ("21/05/26","18:23","GASOIL","VALCARCE-LA JUNQU",E,1.5873,1.0,1.5873,281.87,447.41),
 ("21/05/26","18:32","GASOIL","VALCARCE-LA JUNQU",E,1.5873,1.0,1.5873,355.12,563.68),
 ("23/05/26","17:53","DIESEL","Tournai",E,1.7694,1.0,1.7694,456.00,806.85),
 ("29/05/26","14:15","DIESEL","Zeebrugge",E,1.6843,1.0,1.6843,471.00,793.31)],2632.24)
add("11772 022","Belgium",[
 ("27/05/26","17:28","DIESEL","Zeebrugge",E,1.7694,1.0,1.7694,200.17,354.18),
 ("30/05/26","12:43","DIESEL","Zeebrugge",E,1.6843,1.0,1.6843,385.18,648.76),
 ("30/05/26","12:50","DIESEL","Zeebrugge",E,1.6843,1.0,1.6843,215.24,362.53)],1365.47)
add("11780 023","Germany/Belgium",[
 ("20/05/26","08:36","AdBlue","Lübeck",E,1.2842,1.0,1.2842,42.47,54.54),
 ("28/05/26","19:40","AdBlue","Lübeck",E,1.2842,1.0,1.2842,64.00,82.19),
 ("18/05/26","06:59","DIESEL","Zeebrugge",E,1.7488,1.0,1.7488,300.01,524.66),
 ("20/05/26","08:31","DIESEL","Lübeck",E,1.7403,1.0,1.7403,258.00,449.00),
 ("22/05/26","12:41","DIESEL","Lübeck",E,1.7067,1.0,1.7067,300.05,512.10),
 ("28/05/26","19:31","DIESEL","Lübeck",E,1.6529,1.0,1.6529,550.02,909.13)],2531.62)
add("11806 024","Belgium",[
 ("30/05/26","14:12","AdBlue","Zeebrugge",E,1.3120,1.0,1.3120,20.19,26.49),
 ("19/05/26","10:00","DIESEL","Zeebrugge",E,1.7488,1.0,1.7488,421.09,736.40),
 ("27/05/26","17:22","DIESEL","Waregem",E,1.7694,1.0,1.7694,490.56,868.00),
 ("30/05/26","13:52","DIESEL","Zeebrugge",E,1.6843,1.0,1.6843,490.31,825.83)],2456.72)
add("11814 025","Belgium",[("21/05/26","18:46","DIESEL","Zeebrugge",E,1.8116,1.0,1.8116,470.01,851.47),
 ("30/05/26","18:34","DIESEL","Zeebrugge",E,1.6843,1.0,1.6843,420.01,707.42)],1558.89)
add("11822 026","Belgium",[
 ("20/05/26","07:50","AdBlue","Zeebrugge",E,1.3120,1.0,1.3120,3.01,3.95),
 ("20/05/26","07:55","AdBlue","Zeebrugge",E,1.3120,1.0,1.3120,42.00,55.10),
 ("16/05/26","04:49","DIESEL","Zeebrugge",E,1.7488,1.0,1.7488,330.01,577.12),
 ("20/05/26","07:41","DIESEL","Zeebrugge",E,1.8116,1.0,1.8116,530.05,960.24)],1596.41)
add("11830 027","Belgium",[("21/05/26","12:23","DIESEL","Zeebrugge",E,1.8116,1.0,1.8116,886.00,1605.08),
 ("28/05/26","13:42","DIESEL","Zeebrugge",E,1.7694,1.0,1.7694,690.02,1220.92)],2826.00)
add("11863 032","Belgium",[
 ("22/05/26","10:24","AdBlue","Zeebrugge",E,1.3120,1.0,1.3120,30.20,39.62),
 ("18/05/26","15:44","DIESEL","Zeebrugge",E,1.7488,1.0,1.7488,580.00,1014.30),
 ("22/05/26","10:14","DIESEL","Zeebrugge",E,1.7694,1.0,1.7694,670.00,1185.50),
 ("30/05/26","16:00","DIESEL","Zeebrugge",E,1.6843,1.0,1.6843,860.02,1448.53)],3687.95)
add("11871 033","Belgium",[("18/05/26","10:00","DIESEL","Zeebrugge",E,1.7488,1.0,1.7488,473.87,828.70),
 ("22/05/26","12:41","DIESEL","Zeebrugge",E,1.7694,1.0,1.7694,617.33,1092.30)],1921.00)
add("11889 034","Belgium",[
 ("21/05/26","09:45","AdBlue","Tournai",E,1.3120,1.0,1.3120,51.19,67.16),
 ("21/05/26","09:12","DIESEL","Tournai",E,1.8116,1.0,1.8116,526.16,953.19),
 ("21/05/26","09:24","DIESEL","Tournai",E,1.8116,1.0,1.8116,315.10,570.84)],1591.19)
add("11897 035","Poland/Belgium",[
 ("16/05/26","11:50","AdB. L","Slubice","PLN",4.0019,4.2465,0.9424,50.00,47.12),
 ("30/05/26","12:58","DIESEL","Zeebrugge",E,1.6843,1.0,1.6843,810.01,1364.30)],1411.42)
add("11947 038","Belgium/Austria",[
 ("20/05/26","12:57","DIESEL","Zelzate",E,1.8116,1.0,1.8116,250.11,453.10),
 ("26/05/26","10:04","GASOIL","Sattledt",E,1.7067,1.0,1.7067,460.00,785.08),
 ("26/05/26","10:10","GASOIL","Sattledt",E,1.7067,1.0,1.7067,110.07,187.86)],1426.04)
add("11954 039","Belgium",[("21/05/26","21:19","DIESEL","Zeebrugge",E,1.8116,1.0,1.8116,545.00,987.32),
 ("30/05/26","13:38","DIESEL","Zeebrugge",E,1.6843,1.0,1.6843,730.00,1229.54)],2216.86)
add("11962 040","Belgium",[
 ("25/05/26","16:38","AdBlue","Zeebrugge",E,1.3120,1.0,1.3120,22.77,29.87),
 ("22/05/26","10:38","DIESEL","Zeebrugge",E,1.7694,1.0,1.7694,202.37,358.07),
 ("25/05/26","16:29","DIESEL","Zeebrugge",E,1.7694,1.0,1.7694,234.04,414.11)],802.05)
add("12036 051","Germany",[
 ("19/05/26","16:58","AdBlue","Lübeck",E,1.2842,1.0,1.2842,15.01,19.28),
 ("28/05/26","18:00","AdBlue","Lübeck",E,1.2842,1.0,1.2842,28.03,36.00),
 ("19/05/26","16:54","DIESEL","Lübeck",E,1.7403,1.0,1.7403,252.90,440.12),
 ("22/05/26","14:07","DIESEL","Lübeck",E,1.7067,1.0,1.7067,326.95,558.01),
 ("22/05/26","14:10","DIESEL","Lübeck",E,1.7067,1.0,1.7067,243.56,415.68),
 ("28/05/26","17:56","DIESEL","Lübeck",E,1.6529,1.0,1.6529,292.46,483.41),
 ("30/05/26","09:40","DIESEL","Lübeck",E,1.6513,1.0,1.6513,201.88,333.36)],2285.86)
add("12044 052","France",[
 ("27/05/26","11:37","AdBlue","Chambery",E,0.9820,1.0,0.9820,62.80,61.67),
 ("19/05/26","17:23","Gasoil N","Lyon_Corbas",E,1.9411,1.0,1.9411,726.00,1409.24),
 ("27/05/26","11:24","Gasoil N","Chambery",E,1.8852,1.0,1.8852,580.00,1093.42)],2564.33)
add("12085 055","Denmark",[
 ("18/05/26","06:49","IDS ADBLUE","PADBORG","DKK",8.1400,7.4728,1.0893,15.00,16.34),
 ("18/05/26","06:23","GOEASY IDS TRUCK DIESEL","PADBORG","DKK",13.9942,7.4728,1.8727,270.00,505.62),
 ("18/05/26","06:44","GOEASY IDS TRUCK DIESEL","PADBORG","DKK",13.9942,7.4728,1.8727,170.00,318.36)],840.32)
add("12127 049","Belgium",[
 ("19/05/26","17:28","DIESEL","Liège Herstal",E,1.7488,1.0,1.7488,333.24,582.77),
 ("22/05/26","19:42","DIESEL","Zelzate",E,1.7694,1.0,1.7694,560.00,990.86),
 ("28/05/26","08:36","DIESEL","Zeebrugge",E,1.7694,1.0,1.7694,494.00,874.08)],2447.71)

# Station -> billing country & VAT rate (per the country invoices)
def country_vat(station, product):
    s = station
    if s in ("Zeebrugge","Waregem","Tournai","Zelzate","Bierset_Liège Air","Liège Herstal"): return "Belgium",21.0
    if s == "Lübeck": return "Germany",19.0
    if s in ("Langres_Rolampont","Lyon_Corbas","Chambery"): return "France",20.0
    if s in ("IRUN ARASO","VALCARCE-LA JUNQU"):
        return ("Spain",21.0) if product=="AdBlue" else ("Spain",10.0)
    if s in ("LÅSBY_KALBYGAARD","KØGE_E20","PADBORG"): return "Denmark",25.0
    if s == "Slubice": return "Poland",23.0
    if s == "Sattledt": return "Austria",20.0
    raise ValueError(s)

wb = Workbook()
ws = wb.active
ws.title = "Transactions"
hdr = ["Card","Date","Time","Product","Station","Country","VAT %","Currency",
       "Local price","FX (EUR/cur)","Net price EUR/L","Quantity L",
       "Net amount EUR (doc)","Calc: EUR price x Qty","Diff","Check"]
ws.append(hdr)
fill = PatternFill("solid", start_color="1F4E78")
for c in ws[1]:
    c.font = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    c.fill = fill
    c.alignment = Alignment(horizontal="center", wrap_text=True)

r = 2
for card, cg, d, t, prod, stn, cur, lp, fx, ep, qty, amt, ctot in T:
    ctry, vat = country_vat(stn, prod)
    ws.append([card, d, t, prod, stn, ctry, vat, cur, lp, fx, ep, qty, amt,
               f"=ROUND(K{r}*L{r},2)", f"=N{r}-M{r}", f'=IF(ABS(O{r})<=0.01,"OK","CHECK")'])
    r += 1
last = r - 1
norm = Font(name="Arial", size=10)
for row in ws.iter_rows(min_row=2, max_row=last):
    for c in row: c.font = norm
fmt = {"I":"0.0000","J":"0.0000","K":"0.0000","L":"#,##0.00","M":"#,##0.00","N":"#,##0.00","O":"#,##0.00"}
for col, f in fmt.items():
    for row in ws.iter_rows(min_row=2, max_row=last, min_col=ord(col)-64, max_col=ord(col)-64):
        row[0].number_format = f
for col, w in zip("ABCDEFGHIJKLMNOP",[12,10,7,22,18,10,7,9,10,11,12,11,14,15,9,8]):
    ws.column_dimensions[col].width = w
ws.freeze_panes = "A2"
ws.auto_filter.ref = f"A1:P{last}"

# Card totals check
ws2 = wb.create_sheet("Card totals check")
ws2.append(["Card","Stated card total EUR","Calc: sum of lines","Diff","Check"])
for c in ws2[1]:
    c.font = Font(bold=True, color="FFFFFF", name="Arial", size=10); c.fill = fill
seen = []
for t in T:
    key = (t[0], t[-1])
    if key not in seen: seen.append(key)
r2 = 2
for card, ctot in seen:
    ws2.append([card, ctot,
        f'=ROUND(SUMIF(Transactions!$A$2:$A${last},A{r2},Transactions!$M$2:$M${last}),2)',
        f"=C{r2}-B{r2}", f'=IF(ABS(D{r2})<=0.01,"OK","CHECK")'])
    r2 += 1
last2 = r2 - 1
for row in ws2.iter_rows(min_row=2, max_row=last2):
    for c in row: c.font = norm
    for c in row[1:4]: c.number_format = "#,##0.00"
for col, w in zip("ABCDE",[12,20,20,10,8]):
    ws2.column_dimensions[col].width = w

# Country & VAT summary check (vs payment summary page 1)
ws3 = wb.create_sheet("Country VAT check")
ws3.append(["Country","VAT %","Net (summary doc)","Calc net from transactions","Diff","Check",
            "VAT (doc)","Calc VAT = net x rate","Gross (doc)"])
for c in ws3[1]:
    c.font = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    c.fill = fill; c.alignment = Alignment(wrap_text=True, horizontal="center")
rows = [
 ("Germany",19.0,4292.82,815.63,5108.45),
 ("Belgium",21.0,37955.70,7970.68,45926.38),
 ("France",20.0,2922.77,584.55,3507.32),
 ("Denmark",25.0,3926.55,981.65,4908.20),
 ("Poland",23.0,47.12,10.84,57.96),
 ("Spain (AdBlue)",21.0,155.29,32.61,None),
 ("Spain (GASOIL)",10.0,5784.80,578.49,None),
 ("Austria",20.0,972.94,194.59,1167.53),
]
r3 = 2
for name, vat, net, vatamt, gross in rows:
    cname = name.split(" ")[0]
    if "AdBlue" in name:
        calc = (f'=ROUND(SUMPRODUCT((Transactions!$F$2:$F${last}="Spain")*'
                f'(Transactions!$G$2:$G${last}=21)*Transactions!$M$2:$M${last}),2)')
    elif "GASOIL" in name and cname=="Spain":
        calc = (f'=ROUND(SUMPRODUCT((Transactions!$F$2:$F${last}="Spain")*'
                f'(Transactions!$G$2:$G${last}=10)*Transactions!$M$2:$M${last}),2)')
    else:
        calc = f'=ROUND(SUMIF(Transactions!$F$2:$F${last},"{cname}",Transactions!$M$2:$M${last}),2)'
    ws3.append([name, vat, net, calc, f"=D{r3}-C{r3}",
                f'=IF(ABS(E{r3})<=0.01,"OK","CHECK")', vatamt,
                f"=ROUND(D{r3}*B{r3}/100,2)", gross])
    r3 += 1
ws3.append(["TOTAL","", f"=SUM(C2:C{r3-1})", f"=SUM(D2:D{r3-1})", f"=D{r3}-C{r3}",
            f'=IF(ABS(E{r3})<=0.02,"OK","CHECK")', f"=SUM(G2:G{r3-1})", f"=SUM(H2:H{r3-1})", 67227.03])
tr = r3
for row in ws3.iter_rows(min_row=2, max_row=tr):
    for c in row: c.font = norm
    for c in (row[2],row[3],row[4],row[6],row[7],row[8]): c.number_format = "#,##0.00"
for c in ws3[tr]: c.font = Font(bold=True, name="Arial", size=10)
ws3["A"+str(tr+2)] = ("Controls: Transaction Details net total per document = 56,057.99 EUR; "
                      "payment summary gross total = 67,227.03 EUR. Calc cells recompute from the 94 extracted lines. "
                      "VAT recomputed as net x rate may differ by a few cents due to line-level rounding by Q8.")
ws3["A"+str(tr+2)].font = Font(italic=True, name="Arial", size=9)
ws3.append([])
for col, w in zip("ABCDEFGHI",[16,7,17,24,9,8,12,18,12]):
    ws3.column_dimensions[col].width = w

# Grand total check
ws4 = wb.create_sheet("Grand total check")
ws4.append(["Check item","Document states","Calculated","Diff","Check"])
for c in ws4[1]:
    c.font = Font(bold=True, color="FFFFFF", name="Arial", size=10); c.fill = fill
g = [
 ("Total litres (payment summary)", 32523.14, f"=ROUND(SUM(Transactions!$L$2:$L${last}),2)", 0.01),
 ("Total net EUR (Transaction Details)", 56057.99, f"=ROUND(SUM(Transactions!$M$2:$M${last}),2)", 0.01),
 ("Sum of card totals EUR", 56057.99, f"='Card totals check'!C{last2+1}", 0.01),
 ("Total gross EUR (payment summary)", 67227.03,
  "=ROUND('Country VAT check'!C10+'Country VAT check'!G10,2)", 0.02),
]
ws2[f"B{last2+1}"] = f"=SUM(B2:B{last2})"
ws2[f"C{last2+1}"] = f"=SUM(C2:C{last2})"
ws2[f"A{last2+1}"] = "TOTAL"
for c in ws2[last2+1]: c.font = Font(bold=True, name="Arial", size=10)
ws2[f"B{last2+1}"].number_format = "#,##0.00"
ws2[f"C{last2+1}"].number_format = "#,##0.00"
r4 = 2
for name, doc, calc, tol in g:
    ws4.append([name, doc, calc, f"=C{r4}-B{r4}", f'=IF(ABS(D{r4})<={tol},"OK","CHECK")'])
    r4 += 1
for row in ws4.iter_rows(min_row=2, max_row=r4-1):
    for c in row: c.font = norm
    for c in row[1:4]: c.number_format = "#,##0.00"
for col, w in zip("ABCDE",[36,17,17,10,8]):
    ws4.column_dimensions[col].width = w

wb.save("/home/claude/work/Q8_payment_summary_DE00752298_full_transactions.xlsx")
print("transactions:", last-1, "cards:", last2-1)
