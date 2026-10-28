# Mote v0.5 guide (for the AI that writes it)

A program is lines. A line is a pipeline: `@source | step | step ...`.
Data flows left to right. Last line = the answer. `name=@src|...` saves a result as a new source.

| Sym | Meaning | In -> Out |
|---|---|---|
| `@x` | start from source x (line start only) | -> table |
| `F(cond)` | keep rows where cond true. `&` and, `^` or. ops `== != > < >= <=`; `f~"abc"` text contains, `f!~"abc"` does not contain (ignores case, text must be quoted). Text in quotes | table -> table |
| `P(a,b)` | keep only fields a,b | table -> table |
| `A(n=expr)` | new field n from an expression: fields, numbers, quoted text, `+ - * / % **`, and the functions listed below | table -> table |
| `O(f)` / `D(f)` | sort ascending / descending by f | table -> table |
| `T(n)` | first n rows | table -> table |
| `U(f)` | one row per distinct f (first seen) | table -> table |
| `J(@y,k)` | inner join with source y on field k | table -> table |
| `G(k,S:f)` | group by k, aggregate f into field `v`. Aggs: S sum, C count (`G(k,C)`), V avg, X max, N min, M median, SD std dev, VR variance | table -> table |
| `S(f)` `V(f)` `X(f)` `N(f)` | sum / avg / max / min of f | table -> number |
| `M(f)` | median of f (also `G(k,M:f)`) | table -> number |
| `W(@y,k)` | keep rows whose k is NOT in source y (anti-join) | table -> table |
| `H(f,d)` | add field `pct` = f as % of column total, d decimals | table -> table |
| `B(k,n)` | keep the first n rows for each value of k (sort first to get the top n) | table -> table |
| `K(f)` | add field `cum` = running total of f in current row order | table -> table |
| `E(@y)` | stack source y's rows under this table (both must have the same fields) | table -> table |
| `L(@y,f,tol)` | match each row to the first unused row of y whose number field f differs by at most tol (one-to-one, in order); adds y's other fields. Rows with no match are dropped | table -> table |
| `SD(f)` `VR(f)` | sample standard deviation / variance of f (needs 2+ rows) | table -> number |
| `PC(f,p)` | p-th percentile of f, p from 0 to 100 | table -> number |
| `CO(f,g)` | correlation of two number fields | table -> number |
| `C` | row count | table -> number |
| `R(n)` | round number to n places | number -> number |

Functions for `A` and `Y` expressions (everything else is refused):
- blanks: `fill(f,v)` returns field f with blank cells replaced by v (a number for a number field, quoted text for a text field). Use it to say how blanks should count, e.g. `A(fee2=fill(fee,0))`.
- numbers: `abs(x)` `round(x,n)` `floor(x)` `ceil(x)` `sqrt(x)` `pow(x,n)` `exp(x)` `log(x)` `mod(a,b)` `sign(x)` `least(a,b)` `greatest(a,b)`, and operators `+ - * / % **`. Divide by zero, or sqrt/log of a bad value, stops the run.
- text: `upper(s)` `lower(s)` `trim(s)` `len(s)` `left(s,n)` `right(s,n)` `replace(s,"old","new")` `cat(a,b,...)` joins text/numbers, `num(s)` text to number (stops if it is not a number). `+` does not join text, use `cat`.
- dates (written YYYY-MM-DD): `ym(d)` year-month text like 2026-03, `year(d)` `month(d)` `day(d)`, `weekday(d)` gives Mon..Sun, `addays(d,n)` the date n days later, `days(d1,d2)` whole days from d1 to d2.

Changing data (nothing is saved unless the line ends with `Z`, or is an `I`, `+` or `-` line; the app previews changes first and keeps a backup of anything it overwrites or drops):
| Sym | Meaning |
|---|---|
| `I(name,f1,f2,...)` | create an empty table with these fields (stops if it exists) |
| `+name(v1,v2,...)` | insert one row, values in field order: numbers, or quoted text |
| `-name` | delete the whole table |
| `Q(cond)` | remove the rows where cond is true (same conditions as `F`) |
| `Y(f=expr)` or `Y(f=expr,cond)` | update field f (an existing field) to expr, for all rows or only rows where cond is true; text stays text, numbers stay numbers |
| `Z(name)` | save the current table as new table name (stops if it exists) |
| `Z(name,replace)` | save it over table name (use after `Y` or `Q` to keep the change) |
| `Z(name,add)` | append these rows to table name (same fields) |
Examples: `I(notes,id,text,done)` then `+notes(1,"call bank",0)`; update: `@notes|Y(done=1,id==1)|Z(notes,replace)`; delete: `@notes|Q(done==1)|Z(notes,replace)`; copy: `@tickets|F(status=="open")|Z(open_tickets)`.
Saved line example: `r=@sales|F(returned==1)` then `@sales|U(rep)|W(@r,rep)`.
Careful: after `G` the table only has the key field and `v`. Mote checks fields, not whether your logic matches the question.

Behaviour (defined):
- Sorts are stable: rows with equal values keep their original order, for both `O` and `D`.
- `G` lists groups in first-seen order of the key.
- `L` has the same clash rule as `J`: trim the right source first with `P(f,needed)`.
- `J` drops rows with no match. The right-hand source must have one row per key.
- `J` adds the right source's fields (except the key) to each row. If any of those field names already exist on the left, the run stops. Fix: save a trimmed copy first, keeping the key plus only the fields you need: `x=@y|P(k,needed)` then `@left|J(@x,k)`. A saved line is a source, so this works for a filtered or de-duplicated list too (`x=@t|F(...)|P(k)|U(k)`).
- Blank cells: a blank text cell is the empty text "", so `F(f!="")` keeps rows that have a value and `F(f=="")` finds the blanks. A blank number is an error in any calculation (`A`, `S`, `V`, `X`, `N`, `M`, `G` totals, `K`, `H`, `L`) and in `days()` on a blank date: the run stops and says so. To drop blank-number rows first use a number test such as `F(amt>-1000000000)`. Filters never match a blank number.
- `R(n)` and `round(x,n)` round half to even (13.625 -> 13.62, 13.635 -> 13.64), the same as Python's round.
- `F` with no matches gives an empty table; `C` of an empty table is 0.
- `S`, `V`, `X`, `N`, `M` on an empty table stop with an error.

Rules: one meaning per symbol. No loops, no variables except saved lines.
Errors stop the run and say what is wrong (unknown field, wrong input type).

Example: total paid per category, biggest first
`@orders|F(status=="paid")|G(cat,S:amt)|D(v)`

Beyond tables (Mote Flow):
Beyond tables. Sources start a line; files stay inside the data folder:
`@json("file")` JSON file as a table (nested objects become fields like customer_name; lists stay lists) | `@text("file")` one row per line (fields n, line) | `@dir("folder")` files (fields name, ext written with the dot like .tmp, size in bytes) | `@get("url")` or `@get("url",tries)` JSON from a web address | `@const("a","b")` fixed text rows (field line)
`EX(f)` list field -> one row per item (item fields become f_name) | `RX(f,"regex",new)` one row per regex match (first group if any) | `SP(f,"sep",new)` one row per part | `GT("url/{field}",prefix[,tries])` fetch JSON for every row, fields prefix_name | `LG(f)` adds prev_f (previous row's value) | `TX(f)` all values of f joined by newlines as one text | `WK(children,label)` walk a nested tree of any depth: one row per node, parents first, with its own fields plus depth (root is 0), path (labels joined by /) and is_leaf (1 if no children)
Expressions in A and Y also allow `a if cond else b`, `and`, `or`, `not`, comparisons, and `title(s)` `squash(s)` (collapse spaces).

Additional features: `@dir` also has mtime (YYYY-MM-DD) and path; `LN(pathfield,new)` reads files named in a field; `G(k,C=n,N:d=first,X:d=last)` several named totals; `has(s,"word")` contains for expressions.
Edges: `P(hour=h,n)` renames while projecting; `A(a=x,b=y)` makes several fields in order; `F(size>$m+$d)` can compare against an expression. Programs can also run from a file: python mote_app.py run-file prog.mote --data folder.
Act. `WF("reports/x.csv")` writes the current table (or a single value) to a file inside the data folder; the file type decides the format: .csv, .json, .md (a markdown table `| a | b |` with a `|---|---|` line), .txt (one text value, e.g. from TX). `PT("http://localhost:8080/report")` posts the table as JSON. Put WF or PT last on its line. Nothing is written or posted unless the run is saved (command line: add --apply); without it you only get a preview. Overwritten files are backed up. Script and program file types cannot be written.

Reconcile. `ID(f)` adds a 0-based row-number field f. `RC(@ledger)` (or `RC(@ledger,tol,maxsplit)`, defaults 0.01 and 3) matches the deposits (rows with amount > 0) of the current table, which needs fields description and amount, to the ledger table, which needs reference_number, party_name and amount, in four passes that skip anything already matched: ref_in_description, unique_amount, name_and_amount, split_payment. Rename fields first with P(new=old). It gives one row per match: bank_id (your own bank_id field if the table has one, else the row position), the bank fields, reference_number (several joined by ", "), party_name, match_type, confidence. Matches come in pass order. Unmatched rows are what is left: use W(@matches,bank_id).
Amounts are compared in whole cents, so a payment exactly 1 cent off matches at tolerance 0.01 (comparing floats misses many of these).
