import os
import sys

# Make src/lib importable (macro.py, keymap.py) without packaging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "lib"))
