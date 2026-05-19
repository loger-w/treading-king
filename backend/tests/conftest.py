"""Shared fixtures."""
import os
import sys
from pathlib import Path

# 讓 tests 可以 import backend/ 下的 module 不用裝成 package
sys.path.insert(0, str(Path(__file__).parent.parent))

# 給 user_context.get_user_label() 一個合法值,避免測試時噴 RuntimeError
os.environ.setdefault("USER_LABEL", "test")
