"""ctcbot entry script: dry-run by default; see ``ctcbot/app/cli.py`` and DESIGN.md section 4.D."""
import os
import sys

sys.dont_write_bytecode = True          # before any import: keeps engine/ and laws/ free of __pycache__
_BOT = os.path.dirname(os.path.abspath(__file__))
if _BOT not in sys.path:
    sys.path.insert(0, _BOT)            # python -I does not add the script directory

import ctcbot.app.cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(ctcbot.app.cli.main())
