"""Shared fixtures."""
import sys
from pathlib import Path

# 讓 tests 可以 import backend/ 下的 module 不用裝成 package
sys.path.insert(0, str(Path(__file__).parent.parent))
