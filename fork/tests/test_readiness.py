# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'wait-shora.py'

class ReadinessTests(unittest.TestCase):
    def test_live_listener(self):
        with tempfile.TemporaryDirectory(prefix='shora-ready-') as directory:
            path = str(Path(directory) / 'socket')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(path)
                listener.listen(1)
                result = subprocess.run([str(SCRIPT), '--timeout', '1', path], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
    def test_missing_listener_has_bounded_failure(self):
        with tempfile.TemporaryDirectory(prefix='shora-ready-') as directory:
            path = str(Path(directory) / 'missing')
            result = subprocess.run([str(SCRIPT), '--timeout', '0.1', path], capture_output=True, timeout=2)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'did not become ready', result.stderr)
