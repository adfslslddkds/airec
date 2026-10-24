"""Real IPC/processes with synthetic workers: never imports an audio backend."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

from airec.models import Config, State
from airec.service import Service
from airec.storage import Paths


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = Paths(root / 'config.json', root / 'logs', root / 'records')

    def service(self, code, **options):
        def launch(argv, **kwargs):
            return subprocess.Popen([sys.executable, '-u', '-c', code], **kwargs)
        return Service(self.paths, launch=launch, startup_timeout=.4, stop_timeouts=(.3, .2, .2), **options)

    def wait(self, service, active=False, timeout=5):
        end = time.monotonic() + timeout
        while service.active != active and time.monotonic() < end:
            time.sleep(.01)
        self.assertEqual(service.active, active)

    def wait_state(self, service, state):
        end = time.monotonic() + 3
        while service.state != state and time.monotonic() < end:
            time.sleep(.01)
        self.assertEqual(service.state, state)

    def test_handshake_duplicate_and_clean_stop(self):
        code = 'import json,sys; print(json.dumps({"version":1,"event":"ready"}),flush=True); sys.stdin.readline(); print(json.dumps({"version":1,"event":"exited","clean":True}),flush=True)'
        service = self.service(code)
        self.assertTrue(service.start(Config()))
        self.assertFalse(service.start(Config()))
        self.wait_state(service, State.WAITING)
        service.stop()
        self.wait(service)
        self.assertEqual(service.state, State.STOPPED)

    def test_handshake_timeout(self):
        service = self.service('import time; time.sleep(60)')
        service.start(Config())
        self.wait(service)
        self.assertEqual(service.state, State.ERROR)
        events = list(service.events.queue)
        self.assertIn('готовность', events[-1].detail)

    def test_unexpected_worker_exit(self):
        service = self.service('import sys; sys.exit(7)')
        service.start(Config())
        self.wait(service)
        self.assertEqual(service.state, State.ERROR)

    def test_malformed_protocol(self):
        service = self.service('print("not json",flush=True)')
        service.start(Config())
        self.wait(service)
        self.assertEqual(service.state, State.ERROR)

    def test_tree_escalation_child_ignores_term(self):
        code = '''import subprocess,sys,json,signal,time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'])
print(json.dumps({'version':1,'event':'ready'}),flush=True)
time.sleep(60)
'''
        service = self.service(code)
        service.start(Config())
        self.wait_state(service, State.WAITING)
        service.stop()
        self.wait(service)
        self.assertEqual(service.state, State.ERROR)
        self.assertIn('Принудительная', list(service.events.queue)[-1].detail)

    def test_stop_refusal_retains_ownership_and_retry(self):
        real_tree = None
        from airec.service import ProcessTree
        class RefusingTree(ProcessTree):
            refuse = True
            def signal(self, hard=False):
                if self.refuse:
                    raise PermissionError('test refusal')
                super().signal(hard)
        trees = []
        def factory(process):
            tree = RefusingTree(process)
            trees.append(tree)
            return tree
        service = self.service('import json,time; print(json.dumps({"version":1,"event":"ready"}),flush=True); time.sleep(60)', tree_factory=factory)
        service.start(Config())
        try:
            self.wait_state(service, State.WAITING)
            service.stop()
            self.wait_state(service, State.ERROR)
            self.assertTrue(service.active)
            self.assertFalse(service.start(Config()))
        finally:
            trees[0].refuse = False
            service.stop()
            self.wait(service)


if __name__ == '__main__':
    unittest.main()
