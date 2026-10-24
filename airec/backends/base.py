from typing import Protocol
import sys


class AudioBackend(Protocol):
    def prepare(self): ...
    def activity(self): ...
    def start(self, path): ...
    def check(self): ...
    def stop(self): ...


class UnsupportedBackend:
    def prepare(self):
        raise RuntimeError("Запись на этой ОС пока не реализована")

    def activity(self):
        self.prepare()

    def start(self, path):
        self.prepare()

    def check(self):
        self.prepare()

    def stop(self):
        return True


class WindowsBackend(UnsupportedBackend):
    pass


class MacOSBackend(UnsupportedBackend):
    pass


def get_backend(platform=None):
    platform = platform or sys.platform
    if platform.startswith("linux"):
        from .linux import LinuxBackend
        return LinuxBackend()
    if platform == "win32":
        return WindowsBackend()
    if platform == "darwin":
        return MacOSBackend()
    return UnsupportedBackend()
