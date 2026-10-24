"""Tray callbacks only enqueue commands; they never call Tk."""
import logging
import os
import subprocess
import sys
import threading

from .models import State


class Tray:
    def __init__(self, commands):
        self.commands = commands
        self.icon = None
        self.ready = threading.Event()
        self.thread = None
        self.state = State.STOPPED
        self.reason = "Системный трей недоступен; окно останется открытым"

    @property
    def available(self):
        return self.ready.is_set() and self.thread is not None and self.thread.is_alive()

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="airec-tray")
        self.thread.start()

    def _run(self):
        try:
            if sys.platform == "darwin":
                raise RuntimeError("Интеграция Cocoa-трея с главным циклом Tk не реализована")
            if sys.platform.startswith("linux"):
                # A working library import is not evidence of a desktop tray host.
                result = subprocess.run([
                    "gdbus", "call", "--session", "--dest", "org.kde.StatusNotifierWatcher",
                    "--object-path", "/StatusNotifierWatcher", "--method",
                    "org.freedesktop.DBus.Properties.Get", "org.kde.StatusNotifierWatcher",
                    "IsStatusNotifierHostRegistered"], capture_output=True, text=True,
                    timeout=3, check=True)
                if "true" not in result.stdout.lower():
                    raise RuntimeError("Нет StatusNotifierHost")
                os.environ["PYSTRAY_BACKEND"] = "appindicator"
            import pystray
            from PIL import Image, ImageDraw
            image = Image.new("RGB", (64, 64), "#f4f4f4")
            ImageDraw.Draw(image).ellipse((16, 8, 48, 56), fill="#0873c5")
            def command(name):
                return lambda *_: self.commands.put(name)
            self.icon = pystray.Icon("Airec", image, "Airec", pystray.Menu(
                pystray.MenuItem(lambda _: self.state.value, None, enabled=False),
                pystray.MenuItem("Показать окно", command("show"), default=True),
                pystray.MenuItem(lambda _: "Остановить наблюдение" if self.state in
                                 (State.WAITING, State.RECORDING, State.STOPPING) else "Начать наблюдение",
                                 command("toggle"), enabled=lambda _: self.state not in
                                 (State.STARTING, State.STOPPING)),
                pystray.MenuItem("Выход", command("exit"))))
            def setup(icon):
                icon.visible = True
                self.ready.set()
            self.icon.run(setup=setup)
        except Exception as exc:
            self.reason = f"Трей недоступен: {exc}. Используйте кнопку «Выход»"
            logging.warning(self.reason)
        finally:
            self.ready.clear()
            self.commands.put("show")

    def update(self, state):
        self.state = state
        if self.available:
            try:
                self.icon.title = f"Airec — {state.value}"
                self.icon.update_menu()
            except Exception:
                self.ready.clear()
                self.commands.put("show")
                logging.exception("Tray update failed")

    def stop(self):
        if self.icon:
            self.icon.stop()
        if self.thread:
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                raise RuntimeError("Не удалось завершить системный трей")
