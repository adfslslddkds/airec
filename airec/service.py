import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time

import psutil

from .models import Event, State
from .storage import PROJECT_ROOT


class ProcessTree:
    """A private POSIX session owns worker and capture, including unreported children."""
    def __init__(self, process):
        self.process = process
        self.pgid = process.pid if os.name == "posix" else None
        self.root = psutil.Process(process.pid) if self.pgid is None else None
        self.known = [self.root] if self.root is not None else []

    def members(self):
        if self.pgid is not None:
            members = []
            for proc in psutil.process_iter():
                try:
                    if os.getpgid(proc.pid) == self.pgid and proc.status() != psutil.STATUS_ZOMBIE:
                        members.append(proc)
                except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
                    pass
            return members
        try:
            self.known.extend(self.root.children(recursive=True))
        except psutil.NoSuchProcess:
            pass
        return [p for p in set(self.known) if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]

    def alive(self):
        return bool(self.members())

    def signal(self, hard=False):
        # psutil verifies creation time before signalling, guarding PID reuse.
        for proc in reversed(self.members()):
            try:
                proc.kill() if hard else proc.terminate()
            except psutil.NoSuchProcess:
                pass


class Service:
    def __init__(self, paths, launch=subprocess.Popen, tree_factory=ProcessTree,
                 startup_timeout=12, stop_timeouts=(12, 2, 2)):
        self.paths = paths
        self.launch = launch
        self.tree_factory = tree_factory
        self.startup_timeout = startup_timeout
        self.stop_timeouts = stop_timeouts
        self.events = queue.Queue()
        self.state = State.STOPPED
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self.process = None

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def emit(self, state, detail="", path="", refresh=False):
        self.state = state
        logging.info("%s %s %s", state.value, detail, path)
        self.events.put(Event(state, detail, path, refresh))

    def start(self, config):
        config.validate()
        with self._lock:
            if self.active:
                return False
            self._stop.clear()
            self.emit(State.STARTING)
            self._thread = threading.Thread(target=self._supervise, args=(config,), daemon=False)
            self._thread.start()
            return True

    def stop(self):
        if self.active:
            self._stop.set()

    def _read(self, stream, inbox, diagnostic=False):
        try:
            while line := stream.readline(65537):
                if diagnostic:
                    logging.error("worker stderr: %s", line.rstrip())
                    continue
                if len(line) > 65536:
                    raise ValueError("Слишком длинное сообщение IPC")
                data = json.loads(line)
                if not isinstance(data, dict) or data.get("version") != 1:
                    raise ValueError("Некорректная версия IPC")
                inbox.put(data)
        except Exception as exc:
            inbox.put({"event": "error", "detail": f"Ошибка IPC: {exc}"})
        finally:
            stream.close()
            if not diagnostic:
                inbox.put({"event": "eof"})

    def _wait_tree(self, tree, seconds):
        deadline = time.monotonic() + seconds
        while tree.alive():
            self.process.poll()
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        self.process.wait(timeout=1)
        return True

    def _shutdown(self, tree):
        self.emit(State.STOPPING)
        try:
            self.process.stdin.write('{"version":1,"command":"stop"}\n')
            self.process.stdin.flush()
        except (OSError, ValueError):
            pass
        if self._wait_tree(tree, self.stop_timeouts[0]):
            return False
        for hard, seconds in zip((False, True), self.stop_timeouts[1:]):
            tree.signal(hard)
            if self._wait_tree(tree, seconds):
                return True
        raise RuntimeError("Процессы ещё работают. Повторите остановку; запуск заблокирован")

    def _supervise(self, config):
        tree = None
        inbox = queue.Queue()
        ready = False
        exited = False
        error = ""
        requested = False
        forced = False
        try:
            self.paths.ensure()
            if getattr(sys, "frozen", False):
                import multiprocessing
                multiprocessing.set_executable(sys.executable)
                command = [sys.executable, "--airec-worker", "--config", json.dumps(config.to_dict())]
            else:
                command = [sys.executable, "-u", "-m", "airec.worker", "--config", json.dumps(config.to_dict())]
            self.process = self.launch(
                command, cwd=PROJECT_ROOT,
                env={**os.environ, "AIREC_PROJECT_ROOT": str(PROJECT_ROOT)},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
                start_new_session=os.name == "posix")
            tree = self.tree_factory(self.process)
            for stream, diagnostic in ((self.process.stdout, False), (self.process.stderr, True)):
                threading.Thread(target=self._read, args=(stream, inbox, diagnostic), daemon=True).start()
            deadline = time.monotonic() + self.startup_timeout
            while True:
                if self._stop.is_set():
                    requested = True
                    break
                if not ready and time.monotonic() > deadline:
                    raise RuntimeError("Worker не подтвердил готовность вовремя")
                try:
                    message = inbox.get(timeout=0.05)
                except queue.Empty:
                    if self.process.poll() is not None:
                        raise RuntimeError(f"Worker неожиданно завершён ({self.process.returncode})")
                    continue
                kind = message.get("event")
                if kind == "ready" and not ready:
                    ready = True
                    self.emit(State.WAITING)
                elif kind in ("waiting", "recording_started", "recording_finished") and ready:
                    state = State.RECORDING if kind == "recording_started" else State.WAITING
                    self.emit(state, path=message.get("path", ""), refresh=kind != "waiting")
                elif kind == "error":
                    raise RuntimeError(message.get("detail", "Ошибка worker"))
                elif kind in ("eof", "exited"):
                    raise RuntimeError("Worker неожиданно завершил наблюдение")
                else:
                    raise RuntimeError(f"Нарушение протокола IPC: {kind}")
        except Exception as exc:
            logging.exception("Supervision failed")
            error = str(exc)
        finally:
            if tree is not None:
                while True:
                    self._stop.clear()
                    try:
                        forced = self._shutdown(tree) or forced
                        break
                    except Exception as exc:
                        logging.exception("Shutdown failed")
                        self.emit(State.ERROR, str(exc))
                        # Ownership survives failure; GUI can explicitly retry.
                        while not self._stop.wait(0.1):
                            if not tree.alive():
                                break
                # Readers drain stdout independently while shutdown waits.
                end = time.monotonic() + 0.5
                while time.monotonic() < end:
                    try:
                        message = inbox.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    if message.get("event") == "error":
                        error = message.get("detail", "Ошибка worker")
                    if message.get("event") == "exited":
                        exited = message.get("clean") is True
                try:
                    self.process.stdin.close()
                except OSError:
                    logging.debug("Worker input already closed", exc_info=True)
            if forced:
                error = error or "Принудительная остановка; незавершённые файлы не считаются WAV"
            if requested and not exited and not error:
                error = "Нет подтверждения корректного завершения worker"
            if error:
                self.emit(State.ERROR, error, refresh=True)
            else:
                self.emit(State.STOPPED, refresh=True)
            self.process = None
