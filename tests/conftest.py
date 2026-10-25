"""Shared test fixtures: repository root and import paths for scripts/ and tools/."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for extra in (ROOT, ROOT / "scripts", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

# Prompt ids present in the stock firmware, in the order tools/make_tts.py declares them.
PROMPT_IDS = (0, 1, 2, 3, 8, 9, 11, 12, 14, 15, 17, 18, 19, 20,
              22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 35, 37, 38, 39, 40)
