"""MONTHLY CONFIG - the only file edited each month (besides dropping in new workbooks)."""
PERIOD = "2026-05"
FX = {"PLN": 1/4.27}  # to EUR; update from ECB monthly average
FILES = {
    "Q8":    "Q8_DE00752298_adjusted_pricing.xlsx",  # use ADJUSTED file (rebate cols)
    "BP":    "BP_PL_0261167596_transactions.xlsx",
    "TFC":   "TFC_26056012270_transactions.xlsx",
    "E100":  "E100_BE98759_BE99954_transactions.xlsx",
    "MOEVE": "Moeve_BA72400000187538_transactions.xlsx",
    "DKV":   "DKV_SE_May2026_transactions.xlsx",
}
PAYMENTS = [
    ("2026-06-14","Jupiter Plus AS","Port One (Q8)",54859.20,"EUR","Net after country rebates 12,367.83"),
    ("2026-06-15","SIA OMUSS","BP Poland",72215.36,"PLN","Split payment (MPP)"),
    ("2026-06-15","UAB Vestroidas","E100",45990.04,"EUR","Invoice BE98759 (1-15 May)"),
    ("2026-06-30","UAB Vestroidas","E100",48447.10,"EUR","Invoice BE99954 (16-31 May)"),
    ("2026-06-30","UAB Zaukos Transportas","Moeve Pro",60575.50,"EUR","After 721.65 paid cash at pump"),
    ("per DKV terms","Jupiter Plus AS","DKV",19779.26,"EUR","Inv 26/651689595 (1-15 May)"),
    ("per DKV terms","Jupiter Plus AS","DKV",13757.93,"EUR","Inv 26/652169828 (16-31 May)"),
    ("per contract","UAB Motiejausko Transportas","TFC by Moya",51369.99,"EUR","Invoice 26056012270"),
]
OPEN_ITEMS = [
    "Port One: confirm LU 7.01 EUR rebate (zero LU transactions) + diesel/AdBlue rebate split",
    "Moeve: verify MIJ641 cash payment 721.65 EUR vs cash policy (27-05 Canfranc)",
    "DKV: confirm HVO 100 fill (veh 017, 29-05) was intentional - 43% premium vs diesel",
    "Ask each supplier for monthly CSV/XLS transaction export (kills PDF transcription)",
]
