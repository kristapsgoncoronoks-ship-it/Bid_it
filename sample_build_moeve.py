import os, sys
WORKDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKDIR)
from sample_moeve_data import T, PT
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

wb = Workbook()
ws = wb.active
ws.title = "Transactions"
hdr = ["Plate","Date","Time","Station","Concept","Qty L","PVP EUR/L (incl VAT)",
       "Operation EUR (calc qty x PVP)","Discount EUR (PRN)","Final EUR (incl VAT, doc)",
       "IVA %","Paid cash EUR","Eff. price EUR/L incl VAT","Eff. net price EUR/L excl VAT",
       "Chk1: op-disc=final","Chk2: vs doc op"]
ws.append(hdr)
fill = PatternFill("solid", start_color="0E5FA8")
bw = Font(bold=True, color="FFFFFF", name="Arial", size=9)
norm = Font(name="Arial", size=9)
for c in ws[1]:
    c.font = bw; c.fill = fill; c.alignment = Alignment(horizontal="center", wrap_text=True)

r = 2
for plate, d, t, stn, con, qty, pvp, ce, fin, iva, cash in T:
    ws.append([plate, d, t, stn, con, qty, pvp,
        f"=ROUND(F{r}*G{r},2)", ce, fin, iva, cash if cash else None,
        f"=IF(F{r}>0,J{r}/F{r},\"\")",
        f"=IF(F{r}>0,J{r}/F{r}/(1+K{r}/100),\"\")",
        f'=IF(ABS(ROUND(F{r}*G{r}+I{r},2)-J{r})<=0.015,"OK","CHECK")',
        f'=IF(F{r}=0,"promo line",IF(ABS(H{r}-F{r}*G{r})<=0.011,"OK","CHECK"))'])
    r += 1
last = r - 1
for row in ws.iter_rows(min_row=2, max_row=last):
    for c in row: c.font = norm
for col, f in (("F","#,##0.00"),("G","0.000"),("H","#,##0.00"),("I","0.000"),
               ("J","#,##0.00"),("L","#,##0.00"),("M","0.0000"),("N","0.0000")):
    for row in ws.iter_rows(min_row=2, max_row=last, min_col=ord(col)-64, max_col=ord(col)-64):
        row[0].number_format = f
for col, w in zip("ABCDEFGHIJKLMNOP",[8,10,6,13,11,9,10,11,9,11,6,9,11,11,10,10]):
    ws.column_dimensions[col].width = w
ws.freeze_panes = "A2"
ws.auto_filter.ref = f"A1:P{last}"

# Plate totals check
ws2 = wb.create_sheet("Plate totals check")
ws2.append(["Plate","Stated total EUR","Calc sum of finals","Diff","Check (±0.03 rounding)"])
for c in ws2[1]:
    c.font = bw; c.fill = fill; c.alignment = Alignment(wrap_text=True)
r2 = 2
for plate, tot in PT.items():
    ws2.append([plate, tot,
        f'=ROUND(SUMIF(Transactions!$A$2:$A${last},A{r2},Transactions!$J$2:$J${last}),2)',
        f"=C{r2}-B{r2}", f'=IF(ABS(D{r2})<=0.03,"OK","CHECK")'])
    r2 += 1
ws2.append(["TOTAL", f"=SUM(B2:B{r2-1})", f"=SUM(C2:C{r2-1})", f"=C{r2}-B{r2}",
            f'=IF(ABS(D{r2})<=0.05,"OK","CHECK")'])
last2 = r2
for row in ws2.iter_rows(min_row=2, max_row=last2):
    for c in row: c.font = norm
    for c in row[1:4]: c.number_format = "#,##0.00"
for c in ws2[last2]: c.font = Font(bold=True, name="Arial", size=10)
for col, w in zip("ABCDE",[9,16,17,9,18]):
    ws2.column_dimensions[col].width = w

# Invoice totals check
ws3 = wb.create_sheet("Invoice totals check")
ws3.append(["Check item","Invoice states","Calculated from lines","Diff","Check","Tolerance note"])
for c in ws3[1]:
    c.font = bw; c.fill = fill; c.alignment = Alignment(wrap_text=True)
def q(con): return f'=ROUND(SUMIF(Transactions!$E$2:$E${last},"{con}",Transactions!$F$2:$F${last}),2)'
def op(con): return f'=ROUND(SUMIF(Transactions!$E$2:$E${last},"{con}",Transactions!$H$2:$H${last}),2)'
def fin(con): return f'=ROUND(SUMIF(Transactions!$E$2:$E${last},"{con}",Transactions!$J$2:$J${last}),2)'
items = [
 ("DIESEL STAR litres", 38880.25, q("DIESEL STAR"), 0.01, ""),
 ("DIESEL STAR gross before disc EUR", 68786.96, op("DIESEL STAR"), 0.05, "6-decimal rounding"),
 ("DIESEL STAR final EUR (concept summary 58,008.86)", 58008.86, fin("DIESEL STAR"), 0.05, "6-decimal rounding"),
 ("GASOLEO A litres", 1546.28, q("GASOLEO A"), 0.01, ""),
 ("GASOLEO A gross before disc EUR", 2703.65, op("GASOLEO A"), 0.02, ""),
 ("GASOLEO A final EUR", 2266.99, fin("GASOLEO A"), 0.02, ""),
 ("ECOBLUE litres", 1902.30, q("ECOBLUE"), 0.01, ""),
 ("ECOBLUE gross before disc EUR", 1846.48, op("ECOBLUE"), 0.05, "6-decimal rounding"),
 ("ECOBLUE final EUR (concept summary 1,209.33)", 1209.33, fin("ECOBLUE"), 0.05, "6-decimal rounding"),
 ("Discount at 10% IVA EUR", -11399.35,
  f'=ROUND(SUMIF(Transactions!$K$2:$K${last},10,Transactions!$I$2:$I${last}),2)', 0.02, ""),
 ("Discount at 21% IVA EUR", -640.59,
  f'=ROUND(SUMIF(Transactions!$K$2:$K${last},21,Transactions!$I$2:$I${last}),2)', 0.02, ""),
 ("Final at 10% IVA EUR (gross)", 60091.26,
  f'=ROUND(SUMIF(Transactions!$K$2:$K${last},10,Transactions!$J$2:$J${last}),2)', 0.02, ""),
 ("  implied base 10%", 54628.42, "=ROUND(C13/1.1,2)", 0.05, "doc: 54,628.42 / cuota 5,462.84"),
 ("Final at 21% IVA EUR (gross)", 1205.89,
  f'=ROUND(SUMIF(Transactions!$K$2:$K${last},21,Transactions!$J$2:$J${last}),2)', 0.05, "6-decimal rounding"),
 ("  implied base 21%", 996.61, "=ROUND(C15/1.21,2)", 0.05, "doc: 996.61 / cuota 209.28"),
 ("TOTAL DOCUMENT EUR", 61297.15, f"=ROUND(SUM(Transactions!$J$2:$J${last}),2)", 0.05, "6-decimal rounding"),
 ("Paid cash (al contado) EUR", 721.65, f"=ROUND(SUM(Transactions!$L$2:$L${last}),2)", 0.01, "MIJ641 27-05 fills"),
 ("Amount to settle by transfer EUR", 60575.50, "=C17-C18", 0.05, "due 30-06-2026"),
]
r3 = 2
for name, doc, calc, tol, note in items:
    ws3.append([name, doc, calc, f"=C{r3}-B{r3}", f'=IF(ABS(D{r3})<={tol},"OK","CHECK")', note])
    r3 += 1
for row in ws3.iter_rows(min_row=2, max_row=r3-1):
    for c in row: c.font = norm
    for c in row[1:4]: c.number_format = "#,##0.00"
    row[5].font = Font(italic=True, name="Arial", size=8)
for col, w in zip("ABCDEF",[42,14,16,9,8,26]):
    ws3.column_dimensions[col].width = w

# Price summary
ws4 = wb.create_sheet("Price summary", 0)
ws4.append(["MOEVE PRO BA72400000187538 - UAB Zaukos Transportas - PRICE OVERVIEW (Spain, May 2026)"])
ws4["A1"].font = Font(bold=True, name="Arial", size=11)
ws4.append([])
ws4.append(["Diesel (STAR + Gasoleo A) by station","Litres","Final EUR incl VAT","Eff. EUR/L incl VAT","Eff. NET EUR/L excl VAT (10%)"])
for c in ws4[3]:
    c.font = bw; c.fill = fill; c.alignment = Alignment(wrap_text=True, horizontal="center")
stations = ["OIARTZUN I","OIARTZUN II","CANFRANC","LA JUNQUERA","EMPORDA","IBARBURU",
            "VALLES NORTE","ARAIA","LANZ","VILAMALLA","REQUENA II"]
r4 = 4
for stn in stations:
    b = (f'SUMPRODUCT((Transactions!$D$2:$D${last}=A{r4})*'
         f'((Transactions!$E$2:$E${last}="DIESEL STAR")+(Transactions!$E$2:$E${last}="GASOLEO A"))*')
    ws4.append([stn, f"={b}Transactions!$F$2:$F${last})",
        f"=ROUND({b}Transactions!$J$2:$J${last}),2)", f"=C{r4}/B{r4}", f"=D{r4}/1.1"])
    r4 += 1
tr = r4
ws4.append(["TOTAL DIESEL (excl. April promo lines)", f"=SUM(B4:B{r4-1})", f"=SUM(C4:C{r4-1})",
            f"=C{tr}/B{tr}", f"=D{tr}/1.1"])
r4 += 1
ws4.append(["April promo corrections (5 cts/L)","", -184.04, "", ""])
r4 += 1
ws4.append(["ECOBLUE (AdBlue) all stations",
    f'=SUMIF(Transactions!$E$2:$E${last},"ECOBLUE",Transactions!$F$2:$F${last})',
    f'=ROUND(SUMIF(Transactions!$E$2:$E${last},"ECOBLUE",Transactions!$J$2:$J${last}),2)',
    f"=C{r4}/B{r4}", f"=D{r4}/1.21"])
r4 += 1
for row in ws4.iter_rows(min_row=4, max_row=r4-1):
    for c in row: c.font = norm
    if row[1].value: row[1].number_format = "#,##0.00"
    if row[2].value: row[2].number_format = "#,##0.00"
    row[3].number_format = "0.0000"; row[4].number_format = "0.0000"
for c in ws4[tr]: c.font = Font(bold=True, name="Arial", size=10)
ws4[f"A{r4+1}"] = ("Note: Moeve computes at 6 decimals and rounds displayed amounts; summed rounded lines differ "
                   "from invoice headline figures by max 3 cents - not an error. All amounts include Spanish IVA "
                   "(diesel 10%, AdBlue 21%); discounts (PRN) are off pump PVP. April promo lines (-184.04 EUR) are "
                   "5 cts/L corrections for 13-22 April fills billed in this statement.")
ws4[f"A{r4+1}"].font = Font(italic=True, name="Arial", size=8)
for col, w in zip("ABCDE",[34,12,15,15,17]):
    ws4.column_dimensions[col].width = w

wb.save(os.path.join(WORKDIR, "Moeve_BA72400000187538_transactions.xlsx"))
print("saved", last-1)
