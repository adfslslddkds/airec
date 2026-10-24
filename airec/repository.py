from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import shutil
import uuid
import wave

from .storage import RECORDINGS


@dataclass(frozen=True)
class Recording:
    path: Path
    date: str
    duration: float | None
    problem: str = ""


def duration(path):
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        if frames <= 0 or wav.getframerate() <= 0:
            raise ValueError("Пустая запись")
        expected = frames * wav.getnchannels() * wav.getsampwidth()
        # Stream validation without loading a potentially large file into RAM.
        actual = 0
        while chunk := wav.readframes(65536):
            actual += len(chunk)
        if actual != expected:
            raise ValueError("Обрезанный WAV")
        return frames / wav.getframerate()


class Repository:
    def __init__(self, folder=RECORDINGS):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)

    def managed(self, path, completed=False):
        path = Path(path).absolute()
        if path.is_symlink() or path.parent != self.folder or path.resolve().parent != self.folder:
            raise ValueError("Операция разрешена только для файлов каталога записей, без ссылок")
        if not path.is_file():
            raise FileNotFoundError(f"Файл не найден: {path.name}")
        if completed and path.suffix.lower() != ".wav":
            raise ValueError("Запись не завершена; операция недоступна")
        return path

    def reserve(self):
        path = self.folder / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex + ".partial")
        path.touch(exist_ok=False)
        return path

    def finalize(self, path):
        path = self.managed(path)
        duration(path)
        target = path.with_suffix(".wav")
        path.rename(target)
        return target

    def list(self):
        records = []
        for path in self.folder.iterdir():
            if path.suffix.lower() not in (".wav", ".partial") or path.is_symlink():
                continue
            try:
                stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                problem = "Незавершённая запись (операции заблокированы)"
                seconds = None
                if path.suffix.lower() == ".wav":
                    try:
                        seconds = duration(path)
                        problem = ""
                    except (OSError, EOFError, wave.Error, ValueError) as exc:
                        problem = f"Некорректный WAV: {exc}"
                records.append(Recording(path, stamp, seconds, problem))
            except FileNotFoundError:
                continue
        return sorted(records, key=lambda item: item.path.name, reverse=True)

    def import_wav(self, source):
        source = Path(source).absolute()
        if source.suffix.lower() != ".wav":
            raise ValueError("Поддерживается только WAV")
        if source.resolve().parent == self.folder:
            path = self.managed(source, completed=True)
            duration(path)
            return path
        duration(source)
        target = self.reserve()
        try:
            with source.open("rb") as inp, target.open("wb") as out:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
            return self.finalize(target)
        finally:
            target.unlink(missing_ok=True)

    def playable(self, path):
        path = self.managed(path, completed=True)
        duration(path)
        return path

    def delete(self, path):
        self.managed(path, completed=True).unlink()
