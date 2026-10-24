import logging
import tkinter as tk
from tkinter import messagebox

from .repository import Repository
from .service import Service
from .storage import ConfigStore, Paths, Singleton, configure_logging
from .ui.main import Application


def main():
    lock = None
    try:
        paths = Paths.discover()
        paths.ensure()
        lock = Singleton(paths.config.parent / "instance.lock")
        lock.acquire()
        configure_logging(paths.logs, "application")
        app = Application(Service(paths), ConfigStore(paths.config), Repository(), lock)
    except Exception as exc:
        logging.exception("Startup failed")
        if lock:
            lock.close()
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Airec — Ошибка запуска", str(exc), parent=root)
        root.destroy()
        return
    app.mainloop()


if __name__ == "__main__":
    main()
