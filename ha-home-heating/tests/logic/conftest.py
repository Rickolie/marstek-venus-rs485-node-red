"""The algorithms are plain Python: test them without Home Assistant."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "custom_components" / "home_heating"))
