#!/usr/bin/env python3
"""
Compatibility wrapper.
Use scripts/prod_smoke.py directly for new runs.
"""

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    script = Path(__file__).resolve().parent / "scripts" / "prod_smoke.py"
    cmd = [sys.executable, str(script), "--mode", "quick"]
    env = os.environ.copy()
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
