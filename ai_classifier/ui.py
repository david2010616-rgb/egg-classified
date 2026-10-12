"""Tkinter UI: worker threads never access Tk variables/widgets."""

import csv
import html
import json
import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .backend import BackendError, Cancelled, GeminiBackend, Settings
from .extract import scan_paths
from .learning import Learner
from .organize import Organizer
from .paths import data_dir
from .storage import Store
from .workflow import Workflow

APP_DIR = Path(__file__).resolve().parent.parent


class AIFileSortApp:
    def __init__(self, root, legacy_factory=None, db_path=None):
        self.root, self.legacy_factory = root, legacy_factory
        self.api_key = ""
        root.title("Egg classified🥚")
        root.geometry("1440x920")
        root.minsize(1100, 780)
        self.store = Store(db_path or data_dir() / "ai_state.sqlite3")
        self.organizer, self.learner = Organizer(self.store), Learner(self.store)
        self.events, self.cancel = queue.Queue(), threading.Event()
        self.busy, self.batch_id = False, None
        self.buttons = []
        self.model = tk.StringVar(value=os.getenv("GEMINI_MODEL", ""))
        self.network, self.search, self.cache = (
            tk.BooleanVar(),
            tk.BooleanVar(),
            tk.BooleanVar(value=True),
        )
        self.max_calls, self.max_search = tk.StringVar(value="120"), tk.StringVar(value="20")
        self.destination, self.requirement = tk.StringVar(), tk.StringVar()
        self.category, self.batch_var, self.scheme_var = (
            tk.StringVar(),
            tk.StringVar(),
            tk.StringVar(),
        )
        self.mode = tk.StringVar(value="copy")
        self.status = tk.StringVar(
            value="API setup required: enter your API key and model ID before classification. No provider/model is preconfigured."
        )
        self.network.trace_add("write", self.network_changed)
        self.search.trace_add("write", self.search_changed)
        self._build()
        self.refresh()
        self.root.after(100, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def network_changed(self, *_):
        if self.busy and not self.network.get():
            self.cancel.set()
            self.status.set("Network disabled; stopping after the current request; no new requests will start.")

    def search_changed(self, *_):
        if self.busy and not self.search.get():
            self.cancel.set()
            self.status.set("Search disabled; pausing at the next checkpoint.")

    def button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=lambda: self.guard(command))
        button.pack(side="left", padx=3, pady=3)
        self.buttons.append(button)
        return button

    def guard(self, command):
        if self.busy:
            return
        try:
            command()
        except Exception as exc:
            messagebox.showerror("Unable to continue", str(exc), parent=self.root)

    def row(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=2)
        return frame

    def _build(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text="Egg classified🥚",
            font=("", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="When enabled, Gemini receives filenames, extensions, text excerpts (up to 6000 characters), summaries, scheme instructions and related confirmed examples. "
            "Search sends names, queries and summaries. Original files and full local paths are not uploaded.\n"
            "Disable Allow network to stop new API requests. Allow search controls Google Search separately; one request may perform multiple queries. "
            "Images/scanned PDFs use filenames only unless an OCR adapter is configured.",
            wraplength=1350,
        ).pack(anchor="w", pady=6)
        cfg = self.row(outer)
        ttk.Checkbutton(cfg, text="Allow network", variable=self.network).pack(side="left")
        ttk.Checkbutton(cfg, text="Allow search", variable=self.search).pack(side="left")
        ttk.Checkbutton(cfg, text="Reuse 7-day cache", variable=self.cache).pack(side="left")
        ttk.Label(cfg, text="Model").pack(side="left")
        ttk.Entry(cfg, textvariable=self.model, width=24).pack(side="left")
        ttk.Label(cfg, text="Request limit").pack(side="left")
        ttk.Entry(cfg, textvariable=self.max_calls, width=5).pack(side="left")
        ttk.Label(cfg, text="Search limit").pack(side="left")
        ttk.Entry(cfg, textvariable=self.max_search, width=5).pack(side="left")
        self.button(cfg, "Enter API key", self.set_key)
        self.button(cfg, "Clear response cache", self.clear_cache)
        imports = self.row(outer)
        self.button(imports, "Import folder", self.import_folder)
        self.button(imports, "Import files", self.import_files)
        self.button(imports, "Import names (TXT/CSV)", self.import_names)
        ttk.Label(imports, text="Resume batch").pack(side="left", padx=8)
        self.batch_combo = ttk.Combobox(
            imports, textvariable=self.batch_var, state="readonly", width=38
        )
        self.batch_combo.pack(side="left")
        self.batch_combo.bind("<<ComboboxSelected>>", self.select_batch)
        if self.legacy_factory:
            self.button(
                imports, "Legacy keyword tool", lambda: self.legacy_factory(tk.Toplevel(self.root))
            )
        action = self.row(outer)
        self.button(action, "1. Recognize / resume", self.recognize)
        ttk.Label(action, text="Instructions").pack(side="left", padx=8)
        ttk.Entry(action, textvariable=self.requirement, width=40).pack(
            side="left", fill="x", expand=True
        )
        self.button(action, "2. Suggest schemes", self.propose)
        self.button(action, "Manual scheme", self.manual_scheme)
        schemes = self.row(outer)
        ttk.Label(schemes, text="Scheme").pack(side="left")
        self.scheme_combo = ttk.Combobox(
            schemes, textvariable=self.scheme_var, state="readonly", width=50
        )
        self.scheme_combo.pack(side="left", fill="x", expand=True)
        self.scheme_combo.bind("<<ComboboxSelected>>", self.show_scheme)
        self.button(schemes, "3. Use scheme", self.choose)
        self.button(schemes, "4. Classify / resume", self.classify)
        self.button(schemes, "Offline suggestions", lambda: self.classify(True))
        self.scheme_detail = tk.Text(outer, height=4, wrap="word")
        self.scheme_detail.pack(fill="x", pady=4)
        paths = self.row(outer)
        ttk.Label(paths, text="Output folder").pack(side="left")
        ttk.Entry(paths, textvariable=self.destination).pack(side="left", fill="x", expand=True)
        self.button(paths, "Browse output", self.choose_output)
        self.button(paths, "5. Refresh preview", self.preview)
        columns = ("name", "read", "summary", "category", "status", "target")
        frame = ttk.Frame(outer)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for col, title, width in zip(
            columns,
            ["File / name", "Reading basis", "Recognition summary", "Suggested / confirmed category", "Status", "Destination"],
            [210, 130, 260, 190, 100, 330],
        ):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=60, stretch=True)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.show_item)
        edits = self.row(outer)
        ttk.Label(edits, text="Category (use / for levels; blank accepts suggestion)").pack(side="left")
        ttk.Entry(edits, textvariable=self.category, width=35).pack(
            side="left", fill="x", expand=True
        )
        self.button(edits, "Confirm selected", self.confirm)
        self.button(edits, "Export CSV", self.export)
        self.button(edits, "Search evidence", self.evidence_page)
        self.details = tk.Text(outer, height=7, wrap="word")
        self.details.pack(fill="x")
        bottom = self.row(outer)
        ttk.Radiobutton(bottom, text="Copy (keep originals)", value="copy", variable=self.mode).pack(
            side="left"
        )
        ttk.Radiobutton(bottom, text="Move (undo available)", value="move", variable=self.mode).pack(
            side="left"
        )
        self.button(bottom, "6. Organize / resume", self.execute)
        self.button(bottom, "Undo batch moves", self.undo)
        self.button(bottom, "Train local classifier", self.train)
        self.button(bottom, "Roll back model", self.rollback)
        ttk.Button(bottom, text="Cancel task", command=self.cancel.set).pack(side="right")
        ttk.Label(outer, textvariable=self.status, wraplength=1360).pack(anchor="w", pady=5)

    def settings(self):
        calls, searches = int(self.max_calls.get()), int(self.max_search.get())
        if not 1 <= calls <= 10000 or not 0 <= searches <= calls:
            raise ValueError("Request limit must be 1-10000; search limit must not exceed it")
        return Settings(
            model=self.model.get().strip(),
            network=self.network.get(),
            search=self.search.get(),
            reuse_cache=self.cache.get(),
            max_calls=calls,
            max_search=searches,
        )

    def workflow(self, cfg, batch=None):
        backend = GeminiBackend(self.store, batch or self.batch_id, cfg, self.cancel, api_key=self.api_key)
        return Workflow(
            self.store, backend, self.cancel, lambda m: self.events.put(("progress", m))
        )

    def require_batch(self):
        if not self.batch_id:
            raise ValueError("Import files or select an existing batch first")
        return self.batch_id

    def run(self, work, done=None):
        self.busy = True
        self.cancel.clear()
        for b in self.buttons:
            b.configure(state="disabled")
        self.status.set("Working in the background... Cancel at any time; network timeout is 45 seconds.")

        def worker():
            try:
                result = work()
                self.events.put(("done", (result, done)))
            except (BackendError, Cancelled, ValueError, OSError) as exc:
                self.events.put(("error", str(exc)))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__}: {str(exc)[:300]}"))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "progress":
                    self.status.set(data)
                else:
                    self.busy = False
                    for b in self.buttons:
                        b.configure(state="normal")
                    if kind == "error":
                        self.status.set(data)
                        messagebox.showinfo("Processing paused", data, parent=self.root)
                    else:
                        result, callback = data
                        if callback:
                            callback(result)
                        self.status.set(
                            str(result) if isinstance(result, (str, dict)) else "Completed"
                        )
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def new_import(self, paths=None, manifest=None):
        cfg = self.settings()
        self.run(lambda: self.workflow(cfg).new_batch(paths, manifest), self.import_done)

    def import_done(self, batch):
        self.batch_id = batch["id"]

    def import_folder(self):
        folder = filedialog.askdirectory(parent=self.root)
        if folder:
            output = self.destination.get().strip()
            cfg = self.settings()
            self.run(
                lambda: self.workflow(cfg).new_batch(
                    list(scan_paths(folder, excludes=[APP_DIR, data_dir()] + ([output] if output else [])))
                ),
                self.import_done,
            )

    def import_files(self):
        paths = filedialog.askopenfilenames(parent=self.root)
        if paths:
            self.new_import(paths=list(paths))

    def import_names(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("Name list", "*.txt *.csv")])
        if path:
            self.new_import(manifest=path)

    def select_batch(self, _=None):
        if self.busy:
            return
        self.batch_id = self.batch_lookup.get(self.batch_var.get())
        self.refresh()

    def refresh(self):
        batches = self.store.all("batch")
        self.batch_lookup = {
            f"{b['created_at'][:19]} · {b['id'][:8]}": b["id"] for b in reversed(batches)
        }
        self.batch_combo["values"] = list(self.batch_lookup)
        if not self.batch_id and batches:
            self.batch_id = batches[-1]["id"]
        if self.batch_id:
            self.batch_var.set(
                next((k for k, v in self.batch_lookup.items() if v == self.batch_id), "")
            )
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        batch = self.store.get("batch", self.batch_id) if self.batch_id else None
        for item in self.store.items(self.batch_id) if batch else []:
            p = item.get("prediction") or {}
            category = item.get("category") if item.get("confirmation") else p.get("category", [])
            self.tree.insert(
                "",
                "end",
                iid=item["id"],
                values=(
                    item["name"],
                    item["read_note"],
                    (item.get("recognition") or {}).get("summary", ""),
                    "/".join(category),
                    {
                        "read": "Read",
                        "recognized": "Recognized",
                        "review": "Needs review",
                        "predicted": "Model suggestion",
                        "confirmed": "Confirmed",
                        "done": "Organized",
                    }.get(item["status"], item["status"]),
                    item.get("target") or ("Name-list record; no file to move" if not item["source"] else ""),
                ),
            )
        for ident in selected:
            if self.tree.exists(ident):
                self.tree.selection_add(ident)
        self.scheme_lookup = {}
        for i, p in enumerate((batch or {}).get("proposals", [])):
            self.scheme_lookup[f"Batch suggestion {i + 1}: {p['name']}"] = ("proposal", p)
        for s in self.store.all("scheme"):
            self.scheme_lookup[f"Saved: {s['name']} · {s['id'][:8]}"] = ("saved", s)
        self.scheme_combo["values"] = list(self.scheme_lookup)
        if batch and batch.get("scheme"):
            match = next(
                (
                    k
                    for k, (kind, s) in self.scheme_lookup.items()
                    if kind == "saved" and s["id"] == batch["scheme"]
                ),
                None,
            )
            if match:
                self.scheme_var.set(match)
        elif self.scheme_var.get() not in self.scheme_lookup:
            self.scheme_var.set(next(iter(self.scheme_lookup), ""))
        self.show_scheme()
        if batch:
            usage = self.store.usage(batch["id"])
            self.status.set(
                self.status.get()[:600]
                + f" | API attempts {usage['requests']}; search {usage['search_requests']}"
            )

    def show_scheme(self, _=None):
        value = self.scheme_lookup.get(self.scheme_var.get())
        self.scheme_detail.delete("1.0", "end")
        if value:
            _, s = value
            self.scheme_detail.insert(
                "end",
                f"{s.get('rationale', '')}\nScheme: {s['instruction']}\nExpected folders: "
                + "；".join(s.get("folders", []))
                + "\nExamples: "
                + "；".join(s.get("examples", [])),
            )

    def show_item(self, _=None):
        selected = self.tree.selection()
        if not selected:
            return
        item = self.store.get("item", selected[0])
        self.details.delete("1.0", "end")
        text = {
            k: item.get(k)
            for k in (
                "id",
                "source",
                "fingerprint",
                "read_note",
                "recognition",
                "prediction",
                "confirmation",
            )
        }
        self.details.insert("end", json.dumps(text, ensure_ascii=False, indent=2))
        search = item.get("search_result")
        if search:
            self.details.insert(
                "end",
                "\nQuery time: "
                + search["queried_at"]
                + "\nQuery: "
                + str(search.get("queries"))
                + "\nPrevious queries: "
                + json.dumps(search.get("previous_queries", []), ensure_ascii=False)
                + "\n"
                + search["text"]
                + "\n",
            )
            for i, source in enumerate(search.get("sources", [])):
                tag = "source" + str(i)
                self.details.insert("end", source["title"] + ": " + source["url"] + "\n", tag)
                self.details.tag_config(tag, foreground="blue", underline=True)
                self.details.tag_bind(
                    tag, "<Button-1>", lambda event, url=source["url"]: webbrowser.open(url)
                )
        self.details.insert(
            "end", "\nUsage: " + json.dumps(self.store.usage(item["batch"]), ensure_ascii=False)
        )

    def require_api(self):
        if not self.api_key or not self.model.get().strip():
            raise ValueError(
                "API setup required: use Enter API key and fill in Model before classification. "
                "Only the Gemini adapter is implemented; see docs/API_SETUP.md. "
                "A free Gemini option may be available for your account."
            )

    def recognize(self):
        self.require_api()
        batch, cfg = self.require_batch(), self.settings()
        self.run(
            lambda: (self.workflow(cfg, batch).recognize(batch), "Recognition complete; you can now request schemes")[1]
        )

    def propose(self):
        self.require_api()
        batch, cfg, requirement = self.require_batch(), self.settings(), self.requirement.get()
        self.run(
            lambda: (
                self.workflow(cfg, batch).propose(batch, requirement),
                "Schemes ready; review and select one",
            )[1]
        )

    def manual_scheme(self):
        self.require_api()
        batch = self.require_batch()
        instruction = self.requirement.get().strip() or simpledialog.askstring(
            "Manual scheme", "Enter instructions (for example: group by topic, then year)", parent=self.root
        )
        if instruction:
            self.workflow(self.settings(), batch).choose(
                batch,
                {
                    "name": instruction[:60],
                    "instruction": instruction,
                    "rationale": "User-defined scheme",
                    "folders": [],
                    "examples": [],
                },
            )
            self.refresh()

    def choose(self):
        self.require_api()
        batch = self.require_batch()
        kind, scheme = self.scheme_lookup[self.scheme_var.get()]
        self.workflow(self.settings(), batch).choose(
            batch,
            proposal=scheme if kind == "proposal" else None,
            scheme_id=scheme["id"] if kind == "saved" else None,
        )
        self.refresh()

    def classify(self, local_only=False):
        self.require_api()
        batch, cfg = self.require_batch(), self.settings()
        self.run(
            lambda: (
                self.workflow(cfg, batch).classify(batch, local_only),
                "Classification complete; review and confirm suggestions",
            )[1]
        )

    def choose_output(self):
        folder = filedialog.askdirectory(parent=self.root)
        if folder:
            self.destination.set(folder)

    def preview(self):
        batch, root = self.require_batch(), self.destination.get()
        if not root.strip():
            raise ValueError("Choose an output folder")
        self.run(
            lambda: (self.organizer.preview(batch, root), "Preview updated; refresh again after changing confirmed categories")[1]
        )

    def confirm(self):
        self.require_api()
        ids, category = list(self.tree.selection()), self.category.get().strip()
        if not ids:
            raise ValueError("Select one or more items to confirm")
        rows = [self.store.get("item", ident) for ident in ids]
        labels = [
            (r["id"], category or "/".join((r.get("prediction") or {}).get("category", [])))
            for r in rows
        ]
        if any(not label for _, label in labels):
            raise ValueError("Some items have no suggested category; enter a category")
        if not messagebox.askyesno(
            "Confirm labels",
            f"Confirm {len(ids)} selected items?\nThese labels become trusted examples. Check sources for ambiguous items first.\n"
            + "\n".join(f"{r['name']} → {label}" for r, (_, label) in zip(rows[:8], labels[:8])),
            parent=self.root,
        ):
            return
        cfg, batch = self.settings(), self.require_batch()

        def work():
            flow = self.workflow(cfg, batch)
            for ident, label in labels:
                flow.check()
                flow.confirm(ident, label)
            scheme = self.store.get("batch", batch)["scheme"]
            return self.learner.train(scheme, self.cancel)

        self.run(work)

    def execute(self):
        self.require_api()
        batch, mode = self.require_batch(), self.mode.get()
        rows = [
            r
            for r in self.store.items(batch)
            if r.get("confirmation") and r["source"] and r["status"] != "done"
        ]
        if not rows:
            raise ValueError("No confirmed local files; export name lists as CSV")
        if any(not r.get("target") for r in rows):
            raise ValueError("Refresh the path preview first")
        label = "Copy (keep originals)" if mode == "copy" else "Move (remove originals; undo available)"
        if messagebox.askyesno(
            "Apply previewed operations",
            f"{label}: {len(rows)} confirmed files.\nUnconfirmed items will not be processed.\n"
            + "\n".join(r["target"] for r in rows[:5]),
            parent=self.root,
        ):
            self.run(
                lambda: self.organizer.execute(
                    batch, mode, self.cancel, lambda m: self.events.put(("progress", m))
                )
            )

    def undo(self):
        batch = self.require_batch()
        if messagebox.askyesno(
            "Undo moves", "Restore this batch's moved files. Existing files at original paths will not be overwritten.", parent=self.root
        ):
            self.run(lambda: self.organizer.undo(batch, self.cancel))

    def train(self):
        self.require_api()
        batch = self.store.get("batch", self.require_batch())
        if not batch.get("scheme"):
            raise ValueError("Choose a scheme first")
        self.run(lambda: self.learner.train(batch["scheme"], self.cancel))

    def rollback(self):
        batch = self.store.get("batch", self.require_batch())
        self.status.set(self.learner.rollback(batch.get("scheme")))

    def export(self):
        batch = self.require_batch()
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".csv", filetypes=[("CSV", "*.csv")]
        )
        if not path:
            return

        def work():
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "id",
                        "source",
                        "name",
                        "read_note",
                        "summary",
                        "category",
                        "confirmed",
                        "target",
                        "reason",
                        "sources",
                    ]
                )
                for r in self.store.items(batch):
                    p = r.get("prediction") or {}
                    values = [
                        r["id"],
                        r["source"] or "",
                        r["name"],
                        r["read_note"],
                        (r["recognition"] or {}).get("summary", ""),
                        "/".join(r.get("category", p.get("category", []))),
                        bool(r.get("confirmation")),
                        r.get("target") or "",
                        p.get("reason", ""),
                        json.dumps(
                            (r["search_result"] or {}).get("sources", []), ensure_ascii=False
                        ),
                    ]
                    # Prevent spreadsheet formula execution from untrusted names/content.
                    writer.writerow(
                        [
                            "'" + v
                            if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r"))
                            else v
                            for v in values
                        ]
                    )
            return "Exported: " + path

        self.run(work)

    def evidence_page(self):
        selected = self.tree.selection()
        if not selected:
            raise ValueError("Select an item")
        item = self.store.get("item", selected[0])
        search = item.get("search_result")
        if not search:
            raise ValueError("This item has no online search evidence")
        esc = html.escape
        links = "".join(
            '<li><a rel="noreferrer" href="'
            + esc(s["url"], quote=True)
            + '">'
            + esc(s["title"])
            + "</a></li>"
            for s in search.get("sources", [])
            if s["url"].startswith(("https://", "http://"))
        )
        widgets = "".join(
            '<iframe sandbox="allow-popups allow-popups-to-escape-sandbox" style="width:100%;height:180px;border:0" srcdoc="'
            + esc(widget, quote=True)
            + '"></iframe>'
            for widget in search.get("suggestions", [])
        )
        document = (
            '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; frame-src about:\">"
            + "<title>Search evidence</title><style>body{max-width:960px;margin:30px auto;font:16px system-ui}pre{white-space:pre-wrap}</style>"
            + "<h1>"
            + esc(item["name"])
            + "</h1><p>Query time: "
            + esc(search["queried_at"])
            + "</p><pre>"
            + esc(search["text"])
            + "</pre><ul>"
            + links
            + "</ul>"
            + widgets
        )
        folder = self.store.path.parent / "evidence"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (item["id"] + ".html")
        path.write_text(document, encoding="utf-8")
        webbrowser.open(path.as_uri())

    def set_key(self):
        key = simpledialog.askstring(
            "Kept in memory for this session only",
            "Gemini API key (not saved to files or logs):",
            show="*",
            parent=self.root,
        )
        if key:
            self.api_key = key.strip()
            self.status.set("Key stored for this session only. Enter a model ID, then enable Allow network when ready. Credentials are checked by the provider on the first request.")

    def clear_cache(self):
        self.store.clear_cache()
        self.status.set("Response cache cleared. Batches, evidence and confirmations remain in the local database.")

    def close(self):
        if self.busy:
            self.cancel.set()
            self.status.set("Cancellation requested; wait for the task to save progress, then close again.")
            return
        self.root.destroy()
