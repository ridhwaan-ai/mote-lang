"""Reconciliation step RC on invented data: each of the four passes, ambiguity refusal, the 1-cent edge, and guard rails."""
import pandas as pd

import mote
import mote_fast

bad = 0


def check(name, ok, detail=""):
    global bad
    bad += not ok
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + detail))


inv = pd.DataFrame([
    {"reference_number": "INV-100", "party_name": "Alpha Ltd", "amount": 500.00, "date": "2026-01-01"},
    {"reference_number": "INV-200", "party_name": "Beta Co", "amount": 123.45, "date": "2026-01-01"},
    {"reference_number": "INV-300", "party_name": "Gamma Inc", "amount": 80.00, "date": "2026-01-01"},
    {"reference_number": "INV-400", "party_name": "Delta LLC", "amount": 80.00, "date": "2026-01-01"},
    {"reference_number": "INV-500", "party_name": "Eps Co", "amount": 40.00, "date": "2026-01-01"},
    {"reference_number": "INV-600", "party_name": "Zeta Co", "amount": 60.00, "date": "2026-01-01"},
    {"reference_number": "INV-700", "party_name": "Theta Co", "amount": 1000.01, "date": "2026-01-01"},
])
bank = pd.DataFrame([
    {"date": "d", "description": "PAYMENT inv 100", "amount": 500.00},       # 0 pass 1: reference in description
    {"date": "d", "description": "EFT", "amount": 123.45},                   # 1 pass 2: only one invoice has this amount
    {"date": "d", "description": "EFT FROM GAMMA INC", "amount": 80.00},     # 2 pass 3: amount tied, name breaks the tie
    {"date": "d", "description": "BULK PAY", "amount": 100.00},              # 3 pass 4: INV-500 + INV-600
    {"date": "d", "description": "SALARY", "amount": -900.00},               # 4 withdrawal: ignored
    {"date": "d", "description": "MYSTERY", "amount": 80.00},                # 5 ambiguous at pass 2 (two open 80.00 invoices), passes are not repeated -> stays unmatched
    {"date": "d", "description": "EFT", "amount": 1000.00},                  # 6 one cent off INV-700 -> must match at tolerance 0.01
])
r = mote_fast.run("@bank|RC(@inv,0.01,3)", {"bank": bank, "inv": inv})
got = {x["bank_id"]: (x["reference_number"], x["match_type"]) for x in r}
check("pass 1 ref_in_description", got.get(0) == ("INV-100", "ref_in_description"), str(got.get(0)))
check("pass 2 unique_amount", got.get(1) == ("INV-200", "unique_amount"), str(got.get(1)))
check("pass 3 name_and_amount", got.get(2) == ("INV-300", "name_and_amount"), str(got.get(2)))
check("pass 4 split_payment", got.get(3) == ("INV-500, INV-600", "split_payment"), str(got.get(3)))
check("withdrawal ignored", 4 not in got)
check("ambiguous deposit is left unmatched (passes run once, in order)", 5 not in got, str(got.get(5)))
check("exactly 1 cent off still matches (whole-cent comparison)", got.get(6) == ("INV-700", "unique_amount"), str(got.get(6)))

amb = pd.DataFrame([{"date": "d", "description": "EFT", "amount": 80.00}])
r2 = mote_fast.run("@bank|RC(@inv,0.01,3)", {"bank": amb, "inv": inv})
check("ambiguous amount with no other clue is refused, not guessed", r2 == [], str(r2))

for label, prog in [("bad tolerance", "@bank|RC(@inv,abc)"), ("split too big", "@bank|RC(@inv,0.01,9)"), ("unknown ledger", "@bank|RC(@nope)")]:
    try:
        mote_fast.run(prog, {"bank": bank, "inv": inv})
        check("stops on " + label, False)
    except mote.MoteError:
        check("stops on " + label, True)

print("\nALL PASS" if not bad else f"\n{bad} FAILED")
