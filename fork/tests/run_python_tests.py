#!/usr/bin/env python3
"""Run fork checks; the optional Qt frontend is tested with WIVRN_TEST_QT=1."""
import os
from pathlib import Path
import sys
import unittest

tests = Path(__file__).resolve().parent
sys.path.insert(0, str(tests))
qt_modules = {'test_dashboard', 'test_frame_dashboard', 'test_haritorax_panel',
              'test_portability_dashboard'}
suite = unittest.TestSuite()
for path in sorted(tests.glob('test_*.py')):
    if path.stem in qt_modules and os.environ.get('WIVRN_TEST_QT') != '1':
        print(f'SKIP optional Qt frontend: {path.stem} (set WIVRN_TEST_QT=1)', flush=True)
        continue
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(path.stem))
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(not result.wasSuccessful())
