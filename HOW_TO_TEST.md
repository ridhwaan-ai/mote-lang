# How to test Mote yourself (and watch Claude use it)

## 1. Open the app
Double-click **START_MOTE_APP.bat** in this folder. A page opens at http://127.0.0.1:8765.
It runs on a SANDBOX copy of your trading logs (`data_demo`), so nothing real can be damaged.
Close the black window to stop it.

## 2. Try these (copy into the Program box, press Run)
Read:
- `@history|G(acct,S:usd)|O(v)`            profit per account
- `@history|A(wd=weekday(close_utc))|G(wd,S:usd)|O(v)`   which weekday loses most
- `@history|SD(usd)`                        how spread out results are
- `@outcomes|F(symbol~"xau")|C`             text search, any capitalisation

Change data (these only PREVIEW until you tick "Save changes to disk"):
- `I(shopping,id,item,bought)`              create a table
- `+shopping(1,"milk",0)`                   add a row
- `@shopping|Y(bought=1,id==1)|Z(shopping,replace)`   update a row
- `@shopping|Q(bought==1)|Z(shopping,replace)`        delete rows
- `-shopping`                               delete the table

Make Mote refuse (this is the point of it):
- `@history|A(x=hold_min*2)|C`              blank number, it stops and tells you how to fix it
- `@history|A(x=usd+acct)|C`                text in maths, it stops

Undo a mistake: every overwrite or delete keeps a copy in `data_demo\_backups`.
From a terminal:  `C:\Python314\python.exe mote_app.py restore shopping --data data_demo`

## 3. Watch Claude use it
- Ask Claude in this chat for any question about the data. Claude runs the Mote program through the app and shows you the program, the result and the log line.
- Every run is saved. The "Recent runs" box on the page shows who ran it: `[you]` or `[claude]`.
- Full history: `mote_log.jsonl` (one line per run: time, who, program, result fingerprint, changes).
- To replay a scripted demo of everything: `C:\Python314\python.exe demo.py`

## What Mote cannot do yet (be honest)
Only CSV files, one computer, no passwords or users, no huge data (tested to 100,000 rows), no
automatic scheduling. It is a working prototype, not a finished product.
