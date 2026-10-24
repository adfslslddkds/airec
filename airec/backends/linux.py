import json
import logging
import shutil
import signal
import subprocess
import threading
import time
import uuid


def pactl(*args):
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True,
                              check=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(f"Ошибка PulseAudio/PipeWire (pactl): {detail}") from exc


def external_apps(streams, index, pid=None, token=""):
    apps = set()
    for stream in streams:
        if str(stream.get("source")) != str(index):
            continue
        props = stream.get("properties", {})
        if pid is not None and str(props.get("application.process.id")) == str(pid):
            continue
        if token and props.get("airec.owner") == token:
            continue
        apps.add(props.get("application.name") or "Неизвестное приложение")
    return sorted(apps)


class LinuxBackend:
    def __init__(self):
        self.process = None
        self.mic = None
        self.token = uuid.uuid4().hex
        self.reader = None
        self.tail = ""

    def source(self):
        name = pactl("get-default-source")
        sources = json.loads(pactl("-f", "json", "list", "sources"))
        if not isinstance(sources, list):
            raise RuntimeError("Некорректный JSON списка микрофонов")
        for source in sources:
            if source.get("name") == name:
                monitor = source.get("monitor_of_sink")
                if name.endswith(".monitor") or monitor not in (None, "", 4294967295, "4294967295"):
                    raise RuntimeError("Выбран monitor-источник, а не микрофон")
                return name, source["index"]
        raise RuntimeError("Микрофон по умолчанию не найден")

    def prepare(self):
        for command in ("pactl", "parec"):
            if not shutil.which(command):
                raise RuntimeError(f"Не найдена утилита {command}; установите pulseaudio-utils")
        self.mic = self.source()

    def streams(self):
        streams = json.loads(pactl("-f", "json", "list", "source-outputs"))
        if not isinstance(streams, list):
            raise RuntimeError("Некорректный JSON потоков микрофона")
        return streams

    def activity(self):
        if self.source() != self.mic:
            raise RuntimeError("Микрофон изменён или исчез. Наблюдение остановлено; запустите его заново")
        return external_apps(self.streams(), self.mic[1],
                             self.process.pid if self.process else None, self.token)

    def _diagnostics(self, stream):
        try:
            # Fixed-size reads also handle a child emitting huge lines.
            while chunk := stream.read(4096):
                self.tail = (self.tail + chunk)[-4096:]
                logging.warning("parec: %s", chunk.rstrip())
        finally:
            stream.close()

    def start(self, path):
        self.tail = ""
        self.process = subprocess.Popen([
            "parec", f"--device={self.mic[0]}", "--file-format=wav",
            "--format=s16le", "--rate=44100", "--channels=1",
            f"--property=airec.owner={self.token}", "--client-name=Airec", str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True)
        self.reader = threading.Thread(target=self._diagnostics,
                                       args=(self.process.stderr,), daemon=True)
        self.reader.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.check()
            for stream in self.streams():
                props = stream.get("properties", {})
                if (str(stream.get("source")) == str(self.mic[1])
                        and (props.get("airec.owner") == self.token
                             or str(props.get("application.process.id")) == str(self.process.pid))):
                    self.check()
                    return
            time.sleep(0.05)
        raise RuntimeError("parec не подтвердил подключение к микрофону за 5 секунд")

    def check(self):
        if self.process is not None and self.process.poll() is not None:
            if self.reader:
                self.reader.join(timeout=0.3)
            raise RuntimeError(f"Захват аудио неожиданно завершён ({self.process.returncode}): {self.tail}")

    def stop(self):
        process = self.process
        if process is None:
            return True
        clean = process.poll() is None
        if clean:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                clean = False
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
        if self.reader:
            self.reader.join(timeout=0.5)
        clean = clean and process.returncode == 0
        self.process = None
        return clean
