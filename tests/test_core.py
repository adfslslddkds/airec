import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import wave

from airec.backends.base import get_backend, WindowsBackend, MacOSBackend
from airec.backends.linux import LinuxBackend, external_apps, pactl
from airec.controller import Controller, Tasks
from airec.models import Config, State
from airec.repository import Repository, duration
from airec.service import Service
from airec.storage import ConfigStore, Paths, RECORDINGS, Singleton
from airec.worker import listen, run


def wav(path):
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x00\x00' * 800)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = Repository(self.root / 'recordings')

    def test_paths_cwd_independent(self):
        original = Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(Paths.discover().recordings, RECORDINGS)
            self.assertTrue(RECORDINGS.is_absolute())
            self.assertEqual(RECORDINGS.parent.name, 'airec')
            with patch('airec.storage.sys.frozen', True, create=True), patch(
                    'airec.storage.PlatformDirs') as dirs:
                dirs.return_value.user_data_dir = str(self.root / 'user-data')
                from airec.storage import recordings_path
                self.assertEqual(recordings_path(), self.root / 'user-data' / 'recordings')
        finally:
            os.chdir(original)


    def test_config_validation_and_roundtrip(self):
        store = ConfigStore(self.root / 'settings.json')
        self.assertFalse(store.load().autostart)
        store.save(Config(autostart=True))
        self.assertTrue(store.load().autostart)
        for data in ([], {'schema_version': True}, {'schema_version': 2}, {'interval': float('nan')},
                     {'interval': True}, {'autostart': 1}, {'unknown': 1}):
            with self.assertRaises(ValueError):
                Config.from_dict(data)

    def test_corrupt_config_not_overwritten(self):
        path = self.root / 'settings.json'
        path.write_text('broken')
        store = ConfigStore(path)
        with self.assertRaises(ValueError):
            store.save(Config())
        self.assertEqual(path.read_text(), 'broken')

    def test_atomic_save_failure(self):
        store = ConfigStore(self.root / 'settings.json')
        store.save(Config())
        with patch('airec.storage.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                store.save(Config(autostart=True))
        self.assertFalse(store.load().autostart)
        self.assertEqual(list(self.root.glob('settings-*')), [])

    def test_unique_reservations_and_active_protection(self):
        paths = [self.repo.reserve() for _ in range(20)]
        self.assertEqual(len(set(paths)), 20)
        for operation in (self.repo.delete, self.repo.playable, self.repo.import_wav):
            with self.assertRaises(ValueError):
                operation(paths[0])

    def test_empty_corrupt_and_truncated(self):
        self.assertEqual(self.repo.list(), [])
        (self.repo.folder / 'bad.wav').write_bytes(b'bad')
        (self.repo.folder / 'empty.wav').touch()
        path = self.repo.folder / 'short.wav'
        wav(path)
        path.write_bytes(path.read_bytes()[:-10])
        rows = self.repo.list()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row.problem for row in rows))

    def test_import_copy_self_import_delete(self):
        source = self.root / 'input.wav'
        wav(source)
        copied = self.repo.import_wav(source)
        self.assertEqual(source.read_bytes(), copied.read_bytes())
        self.assertEqual(self.repo.import_wav(copied), copied)
        self.assertEqual(len(self.repo.list()), 1)
        self.assertAlmostEqual(duration(copied), .1)
        self.repo.delete(copied)
        self.assertTrue(source.exists())
        with self.assertRaises(FileNotFoundError):
            self.repo.playable(copied)

    def test_external_and_symlink_protection(self):
        source = self.root / 'input.wav'
        wav(source)
        with self.assertRaises(ValueError):
            self.repo.delete(source)
        link = self.repo.folder / 'link.wav'
        link.symlink_to(source)
        with self.assertRaises(ValueError):
            self.repo.delete(link)
        self.assertTrue(source.exists())
        self.assertEqual(self.repo.list(), [])

    def test_singleton_real_subprocess(self):
        path = self.root / 'lock'
        first = Singleton(path)
        first.acquire()
        code = 'from airec.storage import Singleton; import sys; s=Singleton(sys.argv[1]); s.acquire(); s.close()'
        try:
            result = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
        finally:
            first.close()
        result = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


class BackendTests(unittest.TestCase):
    def test_own_capture_excluded(self):
        streams = [{'source': 4, 'properties': {'application.process.id': '77'}},
                   {'source': '4', 'properties': {'airec.owner': 'token'}},
                   {'source': 4, 'properties': {'application.name': 'Call'}},
                   {'source': 5, 'properties': {'application.name': 'Other'}}]
        self.assertEqual(external_apps(streams, 4, 77, 'token'), ['Call'])

    def test_unsupported_no_linux_calls(self):
        with patch('airec.backends.linux.pactl') as command:
            for platform, cls in [('win32', WindowsBackend), ('darwin', MacOSBackend)]:
                backend = get_backend(platform)
                self.assertIsInstance(backend, cls)
                with self.assertRaisesRegex(RuntimeError, 'пока не реализована'):
                    backend.prepare()
            command.assert_not_called()

    def test_command_timeout_and_missing(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('pactl', 3)):
            with patch('airec.backends.linux.subprocess.run', side_effect=error):
                with self.assertRaises(RuntimeError):
                    pactl('get-default-source')

    def test_monitor_missing_changed_json(self):
        backend = LinuxBackend()
        with patch('airec.backends.linux.pactl', side_effect=['test.monitor', json.dumps([{'name': 'test.monitor', 'index': 1}])]):
            with self.assertRaisesRegex(RuntimeError, 'monitor'):
                backend.source()
        with patch('airec.backends.linux.pactl', side_effect=['missing', '[]']):
            with self.assertRaisesRegex(RuntimeError, 'не найден'):
                backend.source()
        with patch('airec.backends.linux.pactl', return_value='bad'):
            with self.assertRaises(ValueError):
                backend.streams()
        backend.mic = ('old', 1)
        with patch.object(backend, 'source', return_value=('new', 2)):
            with self.assertRaisesRegex(RuntimeError, 'изменён'):
                backend.activity()

    def test_capture_early_exit(self):
        backend = LinuxBackend()
        backend.process = Mock()
        backend.process.poll.return_value = 1
        backend.process.returncode = 1
        with self.assertRaisesRegex(RuntimeError, 'неожиданно'):
            backend.check()

    def test_capture_handshake_args(self):
        backend = LinuxBackend()
        backend.mic = ('mic', 4)
        process = Mock(pid=123, stderr=io.StringIO(''))
        process.poll.return_value = None
        with patch('airec.backends.linux.subprocess.Popen', return_value=process) as launch, patch.object(
                backend, 'streams', return_value=[{'source': 4, 'properties': {'application.process.id': '123'}}]):
            backend.start(Path('/tmp/test.partial'))
        args = launch.call_args.args[0]
        self.assertIn('--file-format=wav', args)
        self.assertEqual(args[-1], '/tmp/test.partial')
        self.assertFalse(any(arg.startswith('--file=') for arg in args))

    def test_capture_escalation_retains_on_refusal(self):
        backend = LinuxBackend()
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('parec', 1)
        backend.process = process
        with self.assertRaises(subprocess.TimeoutExpired):
            backend.stop()
        self.assertIs(backend.process, process)
        process.kill.assert_called_once()


class WorkerTests(unittest.TestCase):
    def test_activity_transitions_without_real_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Repository(temp)
            stop = threading.Event()
            backend = Mock()
            states = iter([[], ['Call'], ['Call'], [], ['Call']])
            def activity():
                try:
                    return next(states)
                except StopIteration:
                    stop.set()
                    return []
            backend.activity.side_effect = activity
            backend.start.side_effect = wav
            backend.stop.return_value = True
            events = []
            run(backend, repo, Config(interval=.001), stop, lambda kind, **data: events.append((kind, data)))
            self.assertEqual([e[0] for e in events], ['ready', 'recording_started', 'recording_finished',
                'waiting', 'recording_started', 'recording_finished', 'waiting', 'exited'])
            self.assertEqual(len(repo.list()), 2)

    def test_no_activity_creates_no_file(self):
        with tempfile.TemporaryDirectory() as temp:
            stop = threading.Event()
            backend = Mock()
            def activity():
                stop.set()
                return []
            backend.activity.side_effect = activity
            run(backend, Repository(temp), Config(), stop, lambda *a, **kw: None)
            backend.start.assert_not_called()
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_capture_failure_keeps_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = Mock()
            backend.activity.return_value = ['Call']
            backend.start.side_effect = RuntimeError('early failure')
            backend.stop.return_value = False
            events = []
            run(backend, Repository(temp), Config(), threading.Event(), lambda kind, **kw: events.append(kind))
            self.assertIn('error', events)
            self.assertNotIn('recording_started', events)
            self.assertEqual(len(list(Path(temp).glob('*.partial'))), 1)

    def test_eof_invalid_and_stop_command(self):
        for text in ('', '{bad}\n', '{"version":1,"command":"stop"}\n'):
            stop = threading.Event()
            listen(io.StringIO(text), stop)
            self.assertTrue(stop.is_set())


class AsyncTests(unittest.TestCase):
    def test_tasks_callbacks_on_main_thread(self):
        tasks = Tasks()
        self.addCleanup(tasks.close)
        main = threading.get_ident()
        results = []
        tasks.submit(threading.get_ident, lambda worker: results.append((worker, threading.get_ident())), self.fail)
        deadline = time.monotonic() + 2
        while tasks.pending and time.monotonic() < deadline:
            tasks.drain()
            time.sleep(.01)
        self.assertNotEqual(results[0][0], main)
        self.assertEqual(results[0][1], main)

    def test_tray_routing_and_state_guards(self):
        service = Mock(state=State.STOPPED, active=False)
        controller = Controller(service, Config())
        show, exit_app = Mock(), Mock()
        controller.dispatch('toggle', show, exit_app)
        service.start.assert_called_once()
        service.state = State.STARTING
        controller.dispatch('toggle', show, exit_app)
        service.start.assert_called_once()
        service.state, service.active = State.WAITING, True
        controller.dispatch('toggle', show, exit_app)
        service.stop.assert_called_once()
        controller.dispatch('show', show, exit_app)
        controller.dispatch('exit', show, exit_app)
        show.assert_called_once()
        exit_app.assert_called_once()


if __name__ == '__main__':
    unittest.main()
