from concurrent.futures import ThreadPoolExecutor
import logging
import queue

from .models import State


class Tasks:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="airec-io")
        self.completed = queue.Queue()
        self.pending = 0

    def submit(self, work, success, failure):
        self.pending += 1
        def run():
            try:
                result = work()
            except Exception as exc:
                logging.exception("Background operation failed")
                self.completed.put(lambda error=exc: failure(error))
            else:
                self.completed.put(lambda: success(result))
        self.pool.submit(run)

    def drain(self):
        while True:
            try:
                callback = self.completed.get_nowait()
            except queue.Empty:
                return
            self.pending -= 1
            callback()

    def close(self):
        self.pool.shutdown(wait=False)


class Controller:
    def __init__(self, service, config):
        self.service = service
        self.config = config
        self.commands = queue.Queue()

    def toggle(self):
        if self.service.state in (State.STARTING, State.STOPPING):
            return
        if self.service.active:
            self.service.stop()
        else:
            self.service.start(self.config)

    def dispatch(self, command, show, exit_app):
        if command == "show":
            show()
        elif command == "toggle":
            self.toggle()
        elif command == "exit":
            exit_app()
