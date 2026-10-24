from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import tempfile

from platformdirs import PlatformDirs
import portalocker

from .models import Config

# Source/editable installations only: package parent is the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECORDINGS = PROJECT_ROOT / "recordings"


@dataclass(frozen=True)
class Paths:
    config: Path
    logs: Path
    recordings: Path = RECORDINGS

    @classmethod
    def discover(cls):
        dirs = PlatformDirs("Airec", appauthor=False)
        return cls(Path(dirs.user_config_dir) / "settings.json", Path(dirs.user_log_dir))

    def ensure(self):
        for path in (self.config.parent, self.logs, self.recordings):
            path.mkdir(parents=True, exist_ok=True)


class ConfigStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            return Config.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return Config()

    def save(self, config):
        data = config.to_dict()
        # Never replace an existing corrupt configuration implicitly.
        self.load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix="settings-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)


class Singleton:
    def __init__(self, path):
        self.lock = portalocker.Lock(str(path), mode="a", timeout=0)

    def acquire(self):
        try:
            self.lock.acquire()
        except portalocker.exceptions.LockException as exc:
            raise RuntimeError("Airec уже запущен для этого пользователя") from exc

    def close(self):
        self.lock.release()


def configure_logging(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / f"{name}.log", maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
