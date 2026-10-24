from dataclasses import replace
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..controller import Controller, Tasks
from ..models import Config, State
from ..platforms import open_path
from ..tray import Tray
from .recordings import RecordingsWindow


class Application(tk.Tk):
    def __init__(self, service, store, repository, singleton, tray_factory=Tray):
        super().__init__()
        self.service, self.store, self.repository, self.singleton = service, store, repository, singleton
        self.tasks = Tasks()
        self.controller = Controller(service, Config())
        self.tray = tray_factory(self.controller.commands)
        self.records = None
        self.loaded = False
        self.exiting = False
        self.finishing = False
        self.closed = False
        self.title("Airec")
        self.geometry("430x510")
        self.resizable(False, False)
        self.configure(bg="#f4f4f4")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f3f3")
        style.configure("TLabel", background="#f3f3f3", foreground="#333333", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=(8, 6), background="#fafafa",
                        foreground="#333333", bordercolor="#d9d9d9")
        style.map("TButton", background=[("active", "#eeeeee")])
        style.configure("Primary.TButton", background="#0873c5", foreground="white", bordercolor="#0873c5")
        style.map("Primary.TButton", background=[("active", "#0867ae")])
        style.configure("TCheckbutton", font=("Segoe UI", 10), background="#f4f4f4")
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=32, background="#fbfbfb", fieldbackground="#fbfbfb")
        style.map("Treeview", background=[("selected", "#0873c5")], foreground=[("selected", "white")])
        body = tk.Frame(self, bg="#f4f4f4", padx=20, pady=18)
        body.pack(fill="both", expand=True)
        self.status = tk.Label(body, text="Готов", bg="#f4f4f4", fg="#333333", font=("Segoe UI", 10, "bold"))
        self.status.pack(anchor="w", pady=(0, 15))
        controls = tk.Frame(body, bg="#f4f4f4")
        controls.pack(fill="x", pady=(0, 12))
        self.record = ttk.Button(controls, text="Начать запись", style="Primary.TButton", command=self.toggle, state="disabled")
        self.record.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(controls, text="Пауза", state="disabled").pack(side="left")
        self.auto = tk.BooleanVar(value=False)
        self.auto_button = ttk.Checkbutton(body, text="Запускать наблюдение при открытии приложения",
                                          variable=self.auto, command=self.save_auto, state="disabled")
        self.auto_button.pack(anchor="w")
        tk.Label(body, text="Звук записывается только при внешней активности микрофона", bg="#f4f4f4",
                 fg="#888888", font=("Segoe UI", 8), wraplength=380).pack(anchor="w", pady=(3, 12))
        for title, action in (("Импортировать...", self.import_recording),
                              ("Открыть папку записей", self.open_folder),
                              ("Управление записями...", self.manage),
                              ("Настройки...", self.settings), ("Выход", self.exit_app)):
            ttk.Button(body, text=title, command=action).pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(body, text="Расшифровка на лету", state="disabled").pack(anchor="w", pady=(7, 0))
        tk.Label(body, text="Пауза, расшифровка, участники и саммари пока недоступны", bg="#f4f4f4",
                 fg="#888888", wraplength=380, font=("Segoe UI", 8)).pack(anchor="w")
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.tasks.submit(store.load, self.config_loaded, self.config_failed)
        self.tray.start()
        self.after(80, self.poll)

    def config_loaded(self, config):
        self.controller.config = config
        self.auto.set(config.autostart)
        self.loaded = True
        self.auto_button.configure(state="normal")
        self.record.configure(state="normal")
        if config.autostart and not self.exiting:
            self.service.start(config)

    def config_failed(self, exc):
        self.error(f"Настройки не загружены и не будут перезаписаны: {exc}\nИсправьте файл {self.store.path}")
        self.record.configure(state="normal")

    def error(self, exc):
        messagebox.showerror("Airec — Ошибка", str(exc), parent=self)

    def toggle(self):
        if not self.exiting:
            self.controller.toggle()

    def save_auto(self):
        config = replace(self.controller.config, autostart=self.auto.get())
        def saved(_):
            self.controller.config = config
        self.tasks.submit(lambda: self.store.save(config), saved, self.error)

    def settings(self):
        messagebox.showinfo("Настройки", "Микрофон: системный по умолчанию.\nПри его смене наблюдение останавливается.\n"
            "Флажок запуска действует при следующем открытии приложения.\n"
            f"Настройки: {self.store.path}\nЛоги: {self.service.paths.logs}", parent=self)

    def import_recording(self):
        if self.exiting:
            return
        name = filedialog.askopenfilename(title="Импортировать WAV", filetypes=[("WAV", "*.wav *.WAV")], parent=self)
        if name:
            self.tasks.submit(lambda: self.repository.import_wav(name), lambda _: self.refresh(), self.error)

    def refresh(self):
        if self.records and self.records.winfo_exists():
            self.records.refresh()

    def open_folder(self):
        self.tasks.submit(lambda: open_path(self.repository.folder), lambda _: None, self.error)

    def manage(self):
        if self.records and self.records.winfo_exists():
            self.records.deiconify()
            self.records.lift()
        else:
            self.records = RecordingsWindow(self)

    def show(self):
        self.deiconify()
        self.lift()

    def hide(self):
        if self.tray.available:
            if self.records and self.records.winfo_exists():
                self.records.withdraw()
            self.withdraw()
        else:
            self.show()
            messagebox.showinfo("Системный трей", self.tray.reason, parent=self)

    def exit_app(self):
        if self.finishing:
            return
        self.exiting = True
        self.record.configure(state="disabled")
        self.auto_button.configure(state="disabled")
        self.service.stop()

    def poll(self):
        self.tasks.drain()
        if self.closed:
            return
        while True:
            try:
                command = self.controller.commands.get_nowait()
            except queue.Empty:
                break
            if command == "toggle" and (self.exiting or not self.loaded):
                continue
            self.controller.dispatch(command, self.show, self.exit_app)
        while True:
            try:
                event = self.service.events.get_nowait()
            except queue.Empty:
                break
            self.status.configure(text=event.state.value, fg="#d64b4b" if event.state in (State.ERROR, State.RECORDING) else "#333333")
            self.record.configure(text="Остановить запись" if self.service.active else "Начать запись",
                                  state="disabled" if self.exiting or event.state in (State.STARTING, State.STOPPING) else "normal")
            self.tray.update(event.state)
            if event.refresh:
                self.refresh()
            if event.state == State.ERROR:
                if self.service.active:
                    self.exiting = False
                    self.record.configure(state="normal", text="Повторить остановку")
                self.error(event.detail)
        if self.exiting and not self.service.active and not self.tasks.pending and not self.finishing:
            self.finishing = True
            def finish():
                if self.loaded:
                    self.store.save(self.controller.config)
                self.tray.stop()
            def done(_):
                self.tasks.close()
                self.singleton.close()
                self.closed = True
                self.destroy()
            def failed(exc):
                self.finishing = self.exiting = False
                self.record.configure(state="normal")
                self.error(exc)
            self.tasks.submit(finish, done, failed)
        if not self.closed:
            if not self.service.active and not self.exiting and self.record.cget("text") != "Начать запись":
                self.record.configure(text="Начать запись", state="normal")
            self.after(80, self.poll)
