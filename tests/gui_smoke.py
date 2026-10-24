"""Run explicitly on a display; synthetic backend and tray, no microphone access."""
from pathlib import Path
import queue
import tempfile
import time
import tkinter as tk
from unittest.mock import Mock, patch

from airec.models import Config, State
from airec.repository import Repository
from airec.storage import Paths, ConfigStore
from airec.ui.main import Application


class FakeTray:
    available = False
    reason = 'Smoke test fallback'
    def __init__(self, commands):
        self.commands = commands
    def start(self): pass
    def update(self, state): pass
    def stop(self): pass


def main():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        paths = Paths(root / 'settings.json', root / 'logs', root / 'recordings')
        service = Mock(state=State.STOPPED, active=False, events=queue.Queue(), paths=paths)
        lock = Mock()
        errors = []
        with patch('airec.ui.main.messagebox.showinfo') as info, patch('airec.ui.main.messagebox.showerror', side_effect=lambda *a, **kw: errors.append(a)):
            app = Application(service, ConfigStore(paths.config), Repository(paths.recordings), lock, FakeTray)
            app.report_callback_exception = lambda *args: errors.append(args)
            def pump(predicate, timeout=3):
                deadline = time.monotonic() + timeout
                while not predicate() and time.monotonic() < deadline:
                    app.update()
                    time.sleep(.01)
                assert predicate()
            pump(lambda: app.loaded)
            service.start.assert_not_called()
            app.manage()
            first = app.records
            app.manage()
            assert app.records is first and isinstance(first, tk.Toplevel)
            pump(lambda: app.tasks.pending == 0)
            assert not first.table.get_children()
            app.hide()
            assert app.state() != 'withdrawn'
            info.assert_called_once()
            app.tray.available = True
            app.hide()
            assert app.state() == 'withdrawn'
            app.controller.commands.put('show')
            pump(lambda: app.state() != 'withdrawn')
            first.destroy()
            assert app.winfo_exists()
            app.auto.set(True)
            app.save_auto()
            pump(lambda: app.tasks.pending == 0)
            assert ConfigStore(paths.config).load().autostart
            service.start.assert_not_called()
            app.exit_app()
            pump(lambda: lock.close.called)
            assert not errors, errors
            print('GUI smoke passed: one Tk, Toplevel reuse, empty list, tray routing/fallback, settings, async exit')


if __name__ == '__main__':
    main()
