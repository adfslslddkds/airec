import tkinter as tk
from tkinter import messagebox, ttk

from ..platforms import open_path


class RecordingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.rows = {}
        self.generation = 0
        self.title("Airec — Записи")
        self.geometry("1040x750")
        self.minsize(760, 570)
        self.configure(bg="#f3f3f3")
        outer = ttk.Frame(self, padding=(16, 14))
        outer.pack(fill="both", expand=True)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))
        for title, action in (("Импортировать...", app.import_recording),
                              ("Открыть папку записей", app.open_folder),
                              ("Обновить", self.refresh)):
            ttk.Button(toolbar, text=title, command=action).pack(side="left", padx=(0, 8))
        ttk.Label(outer, text=str(app.repository.folder), foreground="#888888").pack(anchor="w")
        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=True, pady=8)
        self.table = ttk.Treeview(table_frame, columns=("date", "name", "duration", "status"),
                                 show="headings", selectmode="browse")
        for key, title, width in (("date", "Когда", 170), ("name", "Запись", 350),
                                  ("duration", "Длительность", 100), ("status", "Состояние", 220)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=70)
        scrollbar = ttk.Scrollbar(table_frame, command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.table.bind("<<TreeviewSelect>>", self.selected)
        self.detail = ttk.Label(outer, text="Нет выбранной записи", font=("Segoe UI", 13, "bold"))
        self.detail.pack(anchor="w", pady=(10, 4))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True, pady=8)
        for title in ("Расшифровка", "Участники", "Саммари"):
            frame = ttk.Frame(tabs)
            ttk.Label(frame, text="Данных нет. Функция пока не реализована.", padding=12).pack(anchor="w")
            tabs.add(frame, text=title)
        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        for title in ("Доделать", "Расшифровать заново"):
            ttk.Button(actions, text=title, state="disabled").pack(side="left", padx=(0, 8))
        self.listen = ttk.Button(actions, text="Слушать", command=self.play, state="disabled")
        self.listen.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Открыть папку", command=self.open_selected).pack(side="left", padx=(0, 8))
        self.remove = ttk.Button(actions, text="Удалить", command=self.delete, state="disabled")
        self.remove.pack(side="left")
        self.refresh()

    def refresh(self):
        self.generation += 1
        generation = self.generation
        def loaded(rows):
            if not self.winfo_exists() or generation != self.generation:
                return
            selection = self.table.selection()
            old = self.rows.get(selection[0]).path if selection and selection[0] in self.rows else None
            self.table.delete(*self.table.get_children())
            self.rows = {}
            for i, row in enumerate(rows):
                key = str(i)
                self.rows[key] = row
                self.table.insert("", "end", iid=key, values=(row.date, row.path.name,
                    f"{row.duration:.1f} с" if row.duration is not None else "—",
                    row.problem or "Аудио; расшифровки нет"))
                if row.path == old:
                    self.table.selection_set(key)
            self.selected()
        self.app.tasks.submit(self.app.repository.list, loaded, self.app.error)

    def current(self):
        selected = self.table.selection()
        return self.rows.get(selected[0]) if selected else None

    def selected(self, _event=None):
        row = self.current()
        self.detail.configure(text=row.path.name if row else "Нет выбранной записи")
        self.listen.configure(state="normal" if row and not row.problem else "disabled")
        self.remove.configure(state="normal" if row and row.path.suffix.lower() == ".wav" else "disabled")

    def play(self):
        row = self.current()
        if row:
            self.app.tasks.submit(lambda: open_path(self.app.repository.playable(row.path)),
                                  lambda _: None, self.app.error)

    def open_selected(self):
        row = self.current()
        if row:
            self.app.tasks.submit(lambda: open_path(self.app.repository.managed(row.path).parent),
                                  lambda _: None, self.app.error)

    def delete(self):
        row = self.current()
        if row and messagebox.askyesno("Удалить запись", f"Удалить файл безвозвратно?\n{row.path.name}", parent=self):
            self.app.tasks.submit(lambda: self.app.repository.delete(row.path),
                                  lambda _: self.refresh() if self.winfo_exists() else None, self.app.error)
