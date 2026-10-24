import os
from pathlib import Path
import subprocess
import sys


def open_path(path):
    path = Path(path).resolve(strict=True)
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        command = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.run([command, str(path)], capture_output=True, text=True,
                           check=True, timeout=15)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Не удалось открыть: {exc.stderr.strip()}") from exc
