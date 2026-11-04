#!/usr/bin/env python3
"""从仓库根目录启动无需安装依赖的检索评估器。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
