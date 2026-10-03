#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
import os
import subprocess
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / 'portal/portal-health.py'
spec = importlib.util.spec_from_file_location('portal_health', source)
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)

class PortalHealthTests(unittest.TestCase):
    def test_healthy_is_noop(self):
        state = {}
        self.assertEqual(health.decide(state, 100, True, True), [])
        self.assertEqual(state['failures'], 0)

    def test_missing_frontend_only(self):
        state = {}
        self.assertEqual(health.decide(state, 100, True, False), [])
        self.assertEqual(health.decide(state, 115, True, False), [health.FRONTEND])

    def test_backend_then_frontend_and_budget(self):
        state = {}
        self.assertEqual(health.decide(state, 100, False, False), [])
        self.assertEqual(health.decide(state, 115, False, False), [health.BACKEND, health.FRONTEND])
        self.assertEqual(health.decide(state, 130, False, False), [])
        self.assertEqual(health.decide(state, 145, False, False), [])
        self.assertTrue(health.decide(state, 240, False, False))
        self.assertEqual(health.decide(state, 255, False, False), [])
        self.assertTrue(health.decide(state, 365, False, False))
        self.assertEqual(health.decide(state, 500, False, False), [])
        self.assertEqual(health.decide(state, 600, False, False), [])
        self.assertTrue(health.decide(state, 750, False, False))

    def test_recovery_clears_consecutive_failures(self):
        state = {}
        health.decide(state, 1, False, False)
        health.decide(state, 2, True, True)
        self.assertEqual(health.decide(state, 3, False, False), [])

    def test_logout_never_restarts(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'XDG_RUNTIME_DIR': root}), patch.object(health, 'command') as command:
            command.return_value = subprocess.CompletedProcess([], 3, '', '')
            health.main()
            self.assertEqual(command.call_count, 1)
            self.assertNotIn('restart', command.call_args.args)

    def test_main_restart_order_and_verification(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'XDG_RUNTIME_DIR': root}), patch.object(health, 'command') as command, patch.object(health, 'owner', return_value=True), patch.object(health, 'healthy', return_value=False):
            command.return_value = subprocess.CompletedProcess([], 0, '', '')
            health.main()
            health.main()
            restarts = [c.args[-1] for c in command.call_args_list if 'restart' in c.args]
            self.assertEqual(restarts, [health.BACKEND, health.FRONTEND])

    def test_failed_backend_does_not_restart_frontend(self):
        def command(*args):
            return subprocess.CompletedProcess(args, 1 if 'restart' in args else 0, '', 'failed')
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'XDG_RUNTIME_DIR': root}), patch.object(health, 'command', side_effect=command) as run, patch.object(health, 'owner', return_value=True), patch.object(health, 'healthy', return_value=False):
            health.main(); health.main()
            self.assertEqual([c.args[-1] for c in run.call_args_list if 'restart' in c.args], [health.BACKEND])

if __name__ == '__main__':
    unittest.main()
