"""Run one Mote program on a folder of CSV files and print the result.
Usage:  python mote_run.py DATA_FOLDER "PROGRAM" [--apply]
Without --apply, changes only PREVIEW. Every run is logged in mote_log.jsonl."""
import json
import sys

import mote_app

if len(sys.argv) < 3:
    sys.exit(__doc__)
store = mote_app.Store(sys.argv[1])
out = mote_app.run_program(sys.argv[2], store, apply="--apply" in sys.argv[3:], who="cli")
print(json.dumps(out, indent=1, default=str))
