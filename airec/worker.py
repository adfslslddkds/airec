"""JSON-lines stdout is exclusively a versioned control protocol."""
import argparse
import json
import logging
import signal
import sys
import threading

from .backends.base import get_backend
from .models import Config
from .repository import Repository
from .storage import Paths, configure_logging


def emit(kind, **data):
    print(json.dumps({"version": 1, "event": kind, **data}, ensure_ascii=False), flush=True)


def listen(stream, stop):
    try:
        for line in stream:
            message = json.loads(line)
            if message == {"version": 1, "command": "stop"}:
                break
            raise ValueError("Неизвестная команда IPC")
    except Exception:
        logging.exception("Control channel failed")
    finally:
        stop.set()


def run(backend, repository, config, stop, send=emit):
    path = None
    failed = False

    def finish():
        nonlocal path
        clean = backend.stop()
        if path is not None:
            if not clean:
                incomplete, path = path, None
                raise RuntimeError(f"WAV не финализирован; сохранён незавершённый файл: {incomplete}")
            completed = repository.finalize(path)
            path = None
            send("recording_finished", path=str(completed))

    try:
        backend.prepare()
        if stop.is_set():
            return
        send("ready")
        while not stop.is_set():
            backend.check()
            apps = backend.activity()
            if apps and path is None:
                path = repository.reserve()
                backend.start(path)
                send("recording_started", path=str(path), apps=apps)
            elif not apps and path is not None:
                finish()
                send("waiting")
            stop.wait(config.interval)
    except Exception as exc:
        failed = True
        logging.exception("Recording worker failed")
        send("error", detail=str(exc))
    finally:
        try:
            finish()
        except Exception as exc:
            failed = True
            logging.exception("Audio cleanup failed")
            send("error", detail=str(exc))
        # On EOF there may be no supervisor left to retry cleanup. Retain ownership.
        while getattr(backend, "process", None) is not None and backend.process.poll() is None:
            try:
                backend.stop()
            except Exception:
                logging.exception("Retrying audio cleanup")
                threading.Event().wait(1)
        send("exited", clean=not failed)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    paths = Paths.discover()
    configure_logging(paths.logs, "worker")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    threading.Thread(target=listen, args=(sys.stdin, stop), daemon=True).start()
    try:
        config = Config.from_dict(json.loads(args.config))
        run(get_backend(), Repository(), config, stop)
    except Exception as exc:
        logging.exception("Worker initialization failed")
        emit("error", detail=str(exc))
        emit("exited", clean=False)


if __name__ == "__main__":
    main()
