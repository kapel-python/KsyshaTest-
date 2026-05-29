#!/usr/bin/env python3
"""
Deprecated compatibility wrapper.
Use test.py for the unified test suite.
"""

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    print('[DEPRECATED] full_test.py -> use test.py')
    script = Path(__file__).resolve().parent / 'test.py'
    return subprocess.call([sys.executable, str(script)], env=os.environ.copy())


if __name__ == '__main__':
    raise SystemExit(main())
