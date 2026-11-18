"""Tkinter desktop app for SpaceMap - a read-only treemap of your disk.

Click a rectangle to inspect it, double-click a folder to zoom in, right-click
for Explorer. Nothing in this file writes, moves or deletes a file.
"""

from __future__ import annotations

import os
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import core
from .core import Node, ScanOptions

BACKGROUND = "#f6f7fb"
CARD = "#ffffff"
MUTED = "#64748b"
INK = "#0f172a"
ACCENT_DARK = "#4f46e5"
CANVAS_BG = "#eef1f7"
GRID = "#ffffff"

SIDEBAR_WIDTH = 300
SMALL_CATEGORY_BYTES = 1  # categories smaller than this are still shown


def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass


def _fit(text: str, pixels: float, per_char: float = 6.4) -> str:
    """Shorten text until it fits roughly in ``pixels``."""
    limit = max(3, int(pixels / per_char))
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


class SpaceMapApp(tk.Tk):
    def __init__(self, initial_path: str | None = None) -> None:
        _enable_dpi_awareness()
        super().__init__()
        self.title(f"SpaceMap {core.VERSION}")
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(1200, max(900, screen_w - 160))
        height = min(820, max(600, screen_h - 180))
        self.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{max(20, (screen_h - height) // 3)}")
        self.minsize(860, 560)
        self.configure(bg=BACKGROUND)

        self.result: core.ScanResult | None = None
        self.stack: list[Node] = []
        self.rects: dict[int, Node] = {}
        self.node_rects: list[tuple[Node, int]] = []
        self.selected: Node | None = None
        self.hover_rect: int | None = None
        self.scanning = False
        self.cancel_event = threading.Event()
        self._resize_job: str | None = None
        self._tooltip: tk.Toplevel | None = None
        self._color_cache: dict[int, str] = {}

        self.path_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Choose a folder, then press Scan. Nothing is ever modified.")
        self.info_var = tk.StringVar(value="Hover a rectangle for details · double-click a folder to zoom in")
        self.hidden_var = tk.BooleanVar(value=False)

        self._build_styles()
        self._build_layout()
        self._set_icon()

        start = initial_path
        if not start:
            downloads = Path.home() / "Downloads"
            start = str(downloads if downloads.is_dir() else Path.home())
        self.path_var.set(start)
        self.after(250, lambda: self.start_scan(start))

    # ------------------------------------------------------------------ ui
    def _build_styles(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BACKGROUND, foreground=INK)
        style.configure("Card.TLabel", background=CARD, foreground=INK)
        style.configure("Muted.TLabel", background=BACKGROUND, foreground=MUTED)
        style.configure("H1.TLabel", background=CARD, foreground=INK,
                        font=("Segoe UI Semibold", 19))
        style.configure("Sub.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Badge.TLabel", background="#eef2ff", foreground=ACCENT_DARK,
                        font=("Segoe UI Semibold", 8), padding=(7, 2))
        style.configure("Status.TLabel", background=INK, foreground="#e2e8f0",
                        font=("Segoe UI", 9), padding=(12, 6))
        style.configure("Info.TLabel", background=CARD, foreground="#334155",
                        font=("Segoe UI", 9), padding=(10, 6))
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10))
        style.configure("Treeview", rowheight=25, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 9))

    def _build_layout(self) -> None:
        header = ttk.Frame(self, style="Card.TFrame", padding=(20, 14))
        header.pack(fill="x")
        title_row = ttk.Frame(header, style="Card.TFrame")
        title_row.pack(fill="x")
        ttk.Label(title_row, text="SpaceMap", style="H1.TLabel").pack(side="left")
        ttk.Label(title_row, text="READ-ONLY", style="Badge.TLabel").pack(side="left", padx=(10, 0), pady=(6, 0))
        ttk.Label(
            header,
            text="Every rectangle is a file or folder, sized by how much room it takes.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        body = ttk.Frame(self, padding=(20, 12, 20, 8))
        body.pack(fill="both", expand=True)

        toolbar = ttk.Frame(body)
        toolbar.pack(fill="x")
        self.scan_btn = ttk.Button(toolbar, text="Scan folder…", style="Accent.TButton",
                                   command=self.pick_folder)
        self.scan_btn.pack(side="left")
        self.entry = ttk.Entry(toolbar, textvariable=self.path_var, font=("Segoe UI", 9))
        self.entry.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.entry.bind("<Return>", lambda _e: self.start_scan())
        self.rescan_btn = ttk.Button(toolbar, text="Rescan", command=self.start_scan)
        self.rescan_btn.pack(side="left")
        self.up_btn = ttk.Button(toolbar, text="↑ Up", command=self.go_up, state="disabled")
        self.up_btn.pack(side="left", padx=(6, 0))
        ttk.Checkbutton(toolbar, text="Hidden files", variable=self.hidden_var,
                        command=self._rescan_if_ready).pack(side="left", padx=(12, 0))

        self.crumbs = ttk.Frame(body)
        self.crumbs.pack(fill="x", pady=(8, 6))

        main = ttk.Frame(body)
        main.pack(fill="both", expand=True)

        canvas_card = ttk.Frame(main, style="Card.TFrame", padding=6)
        canvas_card.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_card, bg=CANVAS_BG, highlightthickness=0,
                                bd=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Configure>", self._on_configure)

        sidebar = ttk.Frame(main, width=SIDEBAR_WIDTH)
        sidebar.pack(side="right", fill="y", padx=(12, 0))
        sidebar.pack_propagate(False)

        biggest = ttk.Frame(sidebar, style="Card.TFrame", padding=(10, 8))
        biggest.pack(fill="x")
        ttk.Label(biggest, text="Biggest here", style="Card.TLabel",
                  font=("Segoe UI Semibold", 10)).pack(anchor="w")
        self.side_tree = ttk.Treeview(biggest, columns=("size",), show="tree headings",
                                      selectmode="browse", height=11)
        self.side_tree.heading("#0", text="Item")
        self.side_tree.heading("size", text="Size")
        self.side_tree.column("#0", width=196, stretch=True, anchor="w")
        self.side_tree.column("size", width=76, stretch=False, anchor="e")
        side_scroll = ttk.Scrollbar(biggest, orient="vertical", command=self.side_tree.yview)
        self.side_tree.configure(yscrollcommand=side_scroll.set)
        self.side_tree.pack(side="left", fill="x", expand=True, pady=(6, 0))
        side_scroll.pack(side="right", fill="y", pady=(6, 0))
        self.side_tree.bind("<Double-Button-1>", self._on_side_double_click)
        self.side_tree.bind("<<TreeviewSelect>>", self._on_side_select)

        legend_card = ttk.Frame(sidebar, style="Card.TFrame", padding=(10, 8))
        legend_card.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(legend_card, text="What fills this folder", style="Card.TLabel",
                  font=("Segoe UI Semibold", 10)).pack(anchor="w")
        self.legend_body = ttk.Frame(legend_card, style="Card.TFrame")
        self.legend_body.pack(fill="both", expand=True, pady=(6, 0))

        self.info = ttk.Label(self, textvariable=self.info_var, style="Info.TLabel",
                              anchor="w", relief="flat")
        self.info.pack(fill="x", padx=20, pady=(0, 6))

        bar = ttk.Frame(self, padding=(20, 0, 20, 8))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel",
                  anchor="w").pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=150)
        self.progress.pack_forget()
        self.progress.pack(side="right", padx=(10, 0))
        self.progress.pack_forget()

        self.bind("<Escape>", lambda _e: self.go_up())

    def _set_icon(self) -> None:
        icon = Path(__file__).resolve().parent.parent / "assets" / "spacemap.ico"
        if icon.exists():
            try:
                self.iconbitmap(default=str(icon))
            except Exception:  # noqa: BLE001
                pass

    # -------------------------------------------------------------- scanning
    def _scan_options(self) -> ScanOptions:
        return ScanOptions(
            include_hidden=self.hidden_var.get(),
            exclude_names=list(core.DEFAULT_EXCLUDE),
        )

    def pick_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Folder to map", mustexist=True,
                                         initialdir=self.path_var.get() or str(Path.home()))
        if chosen:
            self.path_var.set(chosen)
            self.start_scan(chosen)

    def _rescan_if_ready(self) -> None:
        if self.result and not self.scanning:
            self.start_scan(str(self.result.root.path))

    def start_scan(self, path: str | None = None) -> None:
        if self.scanning:
            return
        target = (path if path is not None else self.path_var.get()).strip().strip('"')
        if not target:
            messagebox.showwarning("SpaceMap", "Choose a folder first.")
            return
        candidate = Path(target).expanduser()
        if not candidate.is_dir():
            messagebox.showerror("SpaceMap", f"Not a folder:\n{candidate}")
            return

        self.scanning = True
        self.cancel_event.clear()
        self.scan_btn.configure(state="disabled")
        self.rescan_btn.configure(state="disabled")
        self.status_var.set(f"Scanning {candidate} …")
        self.progress.pack(side="right", padx=(10, 0))
        self.progress.start(12)
        self.config(cursor="watch")
        threading.Thread(target=self._scan_worker, args=(candidate, self._scan_options()),
                         daemon=True).start()

    def _scan_worker(self, path: Path, options: ScanOptions) -> None:
        try:
            result = core.scan(
                path, options,
                progress=lambda name, files: self._safe(self._on_progress, files),
                cancel=self.cancel_event.is_set,
            )
        except Exception as exc:  # noqa: BLE001
            self._safe(self._scan_failed, str(exc))
            return
        self._safe(self._scan_done, result)

    def _safe(self, callback, *args) -> None:
        try:
            self.after(0, callback, *args)
        except tk.TclError:
            pass

    def _on_progress(self, files: int) -> None:
        self.status_var.set(f"Scanning… {files:,} files so far")

    def _scan_failed(self, message: str) -> None:
        self._finish_scan()
        self.status_var.set("Scan failed.")
        messagebox.showerror("SpaceMap", message)

    def _scan_done(self, result: core.ScanResult) -> None:
        previous = self.stack[-1].path if self.stack else None
        self.result = result
        self._color_cache.clear()
        self.stack = [result.root]
        if previous is not None:
            found = result.root.find(previous)
            if found is not None and found.is_dir:
                self.stack = self._path_to(found)
        self.selected = None
        self._finish_scan()
        self.path_var.set(str(result.root.path))
        stats = result.stats
        details = [f"{core.human_size(result.size)}", f"{stats.files:,} files",
                   f"{stats.dirs:,} folders", f"{result.elapsed:.1f}s"]
        if stats.hardlinks:
            details.append(f"{stats.hardlinks} hard link(s) counted once")
        if stats.errors:
            details.append(f"{stats.errors} unreadable")
        self.status_var.set("  ·  ".join(details))
        self._render()
        self._update_crumbs()
        self._update_sidebar()

    def _finish_scan(self) -> None:
        self.scanning = False
        self.progress.stop()
        self.progress.pack_forget()
        self.config(cursor="")
        self.scan_btn.configure(state="normal")
        self.rescan_btn.configure(state="normal")

    def _path_to(self, node: Node) -> list[Node]:
        """Chain from the scanned root down to ``node``."""
        if not self.result:
            return [node]
        target = os.path.normcase(os.path.abspath(str(node.path)))
        chain: list[Node] = []

        def walk(current: Node) -> bool:
            chain.append(current)
            if os.path.normcase(os.path.abspath(str(current.path))) == target:
                return True
            for child in current.children:
                if child.is_dir and walk(child):
                    return True
            chain.pop()
            return False

        return chain if walk(self.result.root) else [self.result.root]

    # ---------------------------------------------------------------- drawing
    def _on_configure(self, _event=None) -> None:
        if self._resize_job:
            try:
                self.after_cancel(self._resize_job)
            except Exception:  # noqa: BLE001
                pass
        self._resize_job = self.after(120, self._render)

    def _render(self) -> None:
        self._resize_job = None
        canvas = self.canvas
        canvas.delete("all")
        self.rects.clear()
        self.node_rects.clear()
        self.hover_rect = None

        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 30 or height < 30:
            return
        if not self.stack:
            canvas.create_text(width / 2, height / 2, text="Scan a folder to draw its map",
                               fill=MUTED, font=("Segoe UI", 11))
            return

        node = self.stack[-1]
        if node.size <= 0:
            canvas.create_text(width / 2, height / 2,
                               text=f"{node.path}\n\nNothing to show here (empty or unreadable).",
                               fill=MUTED, font=("Segoe UI", 10), justify="center")
            return

        items = core.treemap_items(node, width - 2, height - 2,
                                   min_fraction=0.0035, max_items=320)
        for child, (x, y, box_w, box_h) in items:
            if box_w < 1 or box_h < 1:
                continue
            if child.is_aggregate:
                fill = "#cbd5e1"
            else:
                fill = self._paint_color(child)
            rect = canvas.create_rectangle(
                x + 1, y + 1, x + box_w - 1, y + box_h - 1,
                fill=fill, outline=GRID, width=1, tags=("cell",),
            )
            self.rects[rect] = child
            self.node_rects.append((child, rect))
            if box_w >= 54 and box_h >= 20:
                label = child.name + ("/" if child.is_dir and not child.is_aggregate else "")
                size_text = core.human_size(child.size)
                canvas.create_text(
                    x + 5, y + 3, text=_fit(label, box_w - 10),
                    anchor="nw", fill=self._text_color(fill),
                    font=("Segoe UI Semibold", 8),
                )
                if box_h >= 34 and box_w >= 70:
                    canvas.create_text(
                        x + 5, y + 16, text=_fit(size_text, box_w - 10, 5.6),
                        anchor="nw", fill=self._text_color(fill, faded=True),
                        font=("Segoe UI", 7),
                    )
        self._apply_highlights()

    def _paint_color(self, node: Node) -> str:
        key = id(node)
        color = self._color_cache.get(key)
        if color is None:
            color = core.node_color(node)
            self._color_cache[key] = color
        return color

    @staticmethod
    def _text_color(fill: str, faded: bool = False) -> str:
        value = fill.lstrip("#")
        try:
            red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
        except ValueError:
            return "#0f172a"
        luminance = 0.299 * red + 0.587 * green + 0.114 * blue
        if luminance > 140:
            return "#475569" if faded else "#0f172a"
        return "#e2e8f0" if faded else "#ffffff"

    def _apply_highlights(self) -> None:
        for child, rect in self.node_rects:
            if self.selected is not None and child is self.selected:
                self.canvas.itemconfigure(rect, outline=INK, width=3)
            elif rect == self.hover_rect:
                self.canvas.itemconfigure(rect, outline="#334155", width=2)
            else:
                self.canvas.itemconfigure(rect, outline=GRID, width=1)

    # ------------------------------------------------------------- interaction
    def _node_at(self, event) -> Node | None:
        current = self.canvas.find_withtag("current")
        if not current:
            return None
        return self.rects.get(current[0])

    def _on_motion(self, event) -> None:
        rid = self.canvas.find_withtag("current")
        rect = rid[0] if rid else None
        if rect == self.hover_rect:
            return
        self.hover_rect = rect
        self._apply_highlights()
        node = self.rects.get(rect) if rect else None
        if node is None:
            self.info_var.set("Hover a rectangle for details · double-click a folder to zoom in")
        else:
            self.info_var.set(self._describe(node, self.stack[-1] if self.stack else None))

    def _on_leave(self, _event) -> None:
        self.hover_rect = None
        self._apply_highlights()

    @staticmethod
    def _describe(node: Node, parent: Node | None) -> str:
        share = (node.size / parent.size * 100) if parent and parent.size else 0.0
        if node.is_aggregate:
            return (f"{node.name} · {core.human_size(node.size)} ({share:.1f}%) · "
                    f"{node.file_count} files too small to draw")
        kind = "folder" if node.is_dir else core.file_category(node.name).lower()
        suffix = f" · {node.file_count:,} files" if node.is_dir else ""
        return f"{node.path}   ·   {core.human_size(node.size)} ({share:.1f}%) · {kind}{suffix}"

    def _on_click(self, event) -> None:
        node = self._node_at(event)
        self.selected = node
        self._apply_highlights()
        if node is None:
            self.info_var.set("Hover a rectangle for details · double-click a folder to zoom in")
            return
        self.info_var.set(self._describe(node, self.stack[-1] if self.stack else None))
        self._highlight_sidebar(node)

    def _on_double_click(self, event) -> None:
        node = self._node_at(event)
        if node is None or node.is_aggregate:
            return
        if node.is_dir:
            self._drill(node)
        else:
            self._reveal(node.path)

    def _on_right_click(self, event) -> None:
        node = self._node_at(event)
        if node is None:
            return
        self.selected = node
        self._apply_highlights()
        self.info_var.set(self._describe(node, self.stack[-1] if self.stack else None))
        self._show_menu(node, event.x_root, event.y_root)

    def _show_menu(self, node: Node, x: int, y: int) -> None:
        menu = tk.Menu(self, tearoff=0)
        if not node.is_aggregate:
            if node.is_dir:
                menu.add_command(label="Open this folder", command=lambda: self._reveal(node.path))
                menu.add_command(label="Zoom in", command=lambda: self._drill(node))
            else:
                menu.add_command(label="Show in Explorer",
                                 command=lambda: self._reveal(node.path))
            menu.add_separator()
            menu.add_command(label="Copy full path",
                             command=lambda: self._copy(str(node.path)))
            menu.add_command(label="Copy name and size",
                             command=lambda: self._copy(f"{node.name}\t{core.human_size(node.size)}"))
        else:
            menu.add_command(label="These files are too small to draw individually",
                             state="disabled")
            menu.add_command(label="Copy folder path", command=lambda: self._copy(str(node.path)))
        menu.tk_popup(x, y)
        menu.grab_release()

    def _copy(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set(f"Copied: {text[:90]}")

    def _reveal(self, path: Path) -> None:
        try:
            if path.is_dir():
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(f'explorer /select,"{os.path.normpath(str(path))}"')
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("SpaceMap", f"Could not open Explorer:\n{exc}")

    # ------------------------------------------------------------- navigation
    def _drill(self, node: Node) -> None:
        if not node.is_dir or node.is_aggregate:
            return
        self.stack = self._path_to(node)
        self.selected = None
        self._render()
        self._update_crumbs()
        self._update_sidebar()

    def go_up(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()
            self.selected = None
            self._render()
            self._update_crumbs()
            self._update_sidebar()

    def _jump(self, index: int) -> None:
        if 0 <= index < len(self.stack):
            self.stack = self.stack[: index + 1]
            self.selected = None
            self._render()
            self._update_crumbs()
            self._update_sidebar()

    def _update_crumbs(self) -> None:
        for child in self.crumbs.winfo_children():
            child.destroy()
        if not self.stack:
            return
        for index, node in enumerate(self.stack):
            if index:
                ttk.Label(self.crumbs, text="›", style="Muted.TLabel").pack(side="left", padx=(2, 0))
            label = node.name if node.name else str(node.path)
            state = "disabled" if index == len(self.stack) - 1 else "normal"
            ttk.Button(self.crumbs, text=_fit(label, 190, 7.0), state=state,
                       command=lambda i=index: self._jump(i)).pack(side="left")
        self.up_btn.configure(state="normal" if len(self.stack) > 1 else "disabled")

    # ---------------------------------------------------------------- sidebar
    def _update_sidebar(self) -> None:
        self.side_tree.delete(*self.side_tree.get_children())
        if not self.stack:
            return
        node = self.stack[-1]
        children = sorted((c for c in node.children if c.size > 0),
                          key=lambda c: (-c.size, c.name.lower()))[:16]
        for child in children:
            self.side_tree.insert(
                "", "end",
                text=("📁 " if child.is_dir else "📄 ") + _fit(child.name, 170, 7.0),
                values=(core.human_size(child.size),),
                iid=str(id(child)),
            )
        self._side_nodes = {str(id(child)): child for child in children}
        self._update_legend(node)

    def _on_side_double_click(self, _event) -> None:
        selection = self.side_tree.selection()
        if not selection:
            return
        node = getattr(self, "_side_nodes", {}).get(selection[0])
        if node is None:
            return
        if node.is_dir:
            self._drill(node)
        else:
            self._reveal(node.path)

    def _on_side_select(self, _event) -> None:
        selection = self.side_tree.selection()
        if not selection:
            return
        node = getattr(self, "_side_nodes", {}).get(selection[0])
        if node is None:
            return
        self.selected = node
        self._apply_highlights()
        self.info_var.set(self._describe(node, self.stack[-1]))

    def _highlight_sidebar(self, node: Node) -> None:
        key = str(id(node))
        if self.side_tree.exists(key):
            self.side_tree.selection_set(key)
            self.side_tree.see(key)

    def _update_legend(self, node: Node) -> None:
        for child in self.legend_body.winfo_children():
            child.destroy()
        rows = self._category_rows(node)
        total = node.size or 1
        if not rows:
            ttk.Label(self.legend_body, text="No files here.", style="Card.TLabel",
                      foreground=MUTED).pack(anchor="w", pady=(4, 0))
            return
        for name, size in rows[:8]:
            row = ttk.Frame(self.legend_body, style="Card.TFrame")
            row.pack(fill="x", pady=1)
            swatch = tk.Frame(row, bg=core.CATEGORY_COLORS.get(name, core.CATEGORY_COLORS["Other"]),
                              width=10, height=10)
            swatch.pack(side="left", padx=(0, 6))
            ttk.Label(row, text=name, style="Card.TLabel", font=("Segoe UI", 9)).pack(side="left")
            ttk.Label(row, text=core.human_size(size), style="Card.TLabel",
                      font=("Segoe UI", 9), foreground=MUTED).pack(side="right")
            percent = size * 100 / total
            bar = tk.Frame(self.legend_body, bg="#e2e8f0", height=3)
            bar.pack(fill="x", pady=(0, 4))
            fill = tk.Frame(bar, bg=core.CATEGORY_COLORS.get(name, core.CATEGORY_COLORS["Other"]),
                            height=3)
            fill.place(relwidth=max(0.01, min(1.0, percent / 100)), relheight=1)

    @staticmethod
    def _category_rows(node: Node) -> list[tuple[str, int]]:
        sizes: dict[str, int] = {}
        for child in node.walk():
            if child.is_dir:
                continue
            category = core.file_category(child.name)
            sizes[category] = sizes.get(category, 0) + child.size
        return sorted(((name, size) for name, size in sizes.items() if size >= SMALL_CATEGORY_BYTES),
                      key=lambda pair: -pair[1])


def main(initial_path: str | None = None) -> None:
    app = SpaceMapApp(initial_path)
    app.mainloop()


if __name__ == "__main__":
    main()
