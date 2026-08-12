"""Entry point for the launchers.

Run by the isolated Open Interpreter environment, which knows nothing about this
repository. Its only job is to put ``src`` on the import path and hand over to
``hm_oi.launch``, so the real logic has one home and one set of tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from hm_oi.launch import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
