"""SpaceMap core engine.

A read-only disk usage scanner plus the treemap maths that turns a folder into
rectangles. Nothing in this module deletes, moves or writes anything.

Highlights:
  * os.scandir walk (fast, no full Path objects per entry)
  * symlinks are skipped, hard links are counted once so numbers match the disk
  * squarified treemap layout (Bruls, Huizing & van Wijk) with tests
  * tree/JSON/CSV export and a "largest items" view
"""

from __future__ import annotations

import csv
import json
import os
import stat as stat_module
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Sequence

APP_NAME = "SpaceMap"
VERSION = "1.0.0"
PROJECT_URL = "https://github.com/hassanelsayed91/spacemap"

SIZE_UNITS = {
    "": 1, "b": 1,
    "k": 1024, "kb": 1024,
    "m": 1024 ** 2, "mb": 1024 ** 2,
    "g": 1024 ** 3, "gb": 1024 ** 3,
    "t": 1024 ** 4, "tb": 1024 ** 4,
}

DEFAULT_EXCLUDE = [
    "$RECYCLE.BIN", "System Volume Information", "node_modules", ".git",
    "__pycache__", ".venv", "venv",
]

# extension -> category, used for colouring and for the legend
CATEGORY_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "Images": (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico",
               ".tif", ".tiff", ".heic", ".avif", ".psd", ".raw"),
    "Video": (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
              ".mpg", ".mpeg", ".3gp"),
    "Audio": (".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus"),
    "Archives": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso",
                 ".tgz", ".cab"),
    "Installers": (".exe", ".msi", ".msix", ".appx", ".apk", ".dmg", ".deb"),
    "Documents": (".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls",
                  ".xlsx", ".ppt", ".pptx", ".csv", ".md", ".epub"),
    "Code": (".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json",
             ".xml", ".yaml", ".yml", ".sql", ".ps1", ".bat", ".sh", ".c",
             ".h", ".cpp", ".cs", ".java", ".php", ".go", ".rs"),
    "System": (".dll", ".sys", ".dat", ".log", ".tmp", ".bin", ".db"),
}

CATEGORY_COLORS: dict[str, str] = {
    "Images": "#0ea5e9",
    "Video": "#8b5cf6",
    "Audio": "#ec4899",
    "Archives": "#f59e0b",
    "Installers": "#ef4444",
    "Documents": "#10b981",
    "Code": "#6366f1",
    "System": "#64748b",
    "Folder": "#94a3b8",
    "Other": "#14b8a6",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024 or unit == "TB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def parse_size(text: str | int) -> int:
    """'500mb' / '2 GB' / '1024' -> bytes."""
    if isinstance(text, int):
        return text
    raw = str(text).strip().lower().replace("_", "").replace(" ", "")
    number = "".join(ch for ch in raw if ch.isdigit() or ch == ".")
    unit = "".join(ch for ch in raw if ch.isalpha())
    if not number:
        raise ValueError(f"invalid size: {text}")
    if unit not in SIZE_UNITS:
        raise ValueError(f"unknown unit in {text!r} (use B, KB, MB, GB or TB)")
    return int(float(number) * SIZE_UNITS[unit])


def file_category(name: str) -> str:
    suffix = Path(name).suffix.lower()
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if suffix in extensions:
            return category
    return "Other"


def category_color(name: str, is_dir: bool = False) -> str:
    return CATEGORY_COLORS["Folder"] if is_dir else CATEGORY_COLORS.get(
        file_category(name), CATEGORY_COLORS["Other"]
    )


def dominant_category(node: "Node") -> str:
    """Which file type takes the most room inside a folder."""
    if not node.is_dir:
        return file_category(node.name)
    sizes: Counter = Counter()
    for child in node.walk():
        if child.is_dir:
            continue
        sizes[file_category(child.name)] += child.size
    if not sizes:
        return "Folder"
    return max(sizes.items(), key=lambda pair: pair[1])[0]


def lighten(color: str, amount: float = 0.35) -> str:
    """Mix a #rrggbb colour with white; amount 0 = unchanged, 1 = white."""
    value = color.lstrip("#")
    if len(value) != 6:
        return color
    channels = [int(value[index:index + 2], 16) for index in (0, 2, 4)]
    mixed = [round(channel + (255 - channel) * amount) for channel in channels]
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def node_color(node: "Node") -> str:
    """Folder = its dominant content type (lighter), file = its own type."""
    category = dominant_category(node)
    color = CATEGORY_COLORS.get(category, CATEGORY_COLORS["Other"])
    return lighten(color, 0.28) if node.is_dir else color



def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    if os.name != "nt":
        return False
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
    except OSError:
        return False
    return bool(attrs & (stat_module.FILE_ATTRIBUTE_HIDDEN | stat_module.FILE_ATTRIBUTE_SYSTEM))


# --------------------------------------------------------------------------- #
# tree model
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    name: str
    path: Path
    size: int = 0
    is_dir: bool = True
    file_count: int = 0
    children: list["Node"] = field(default_factory=list)
    error: str = ""
    is_aggregate: bool = False

    def sort_children(self) -> None:
        self.children.sort(key=lambda child: (-child.size, child.name.lower()))
        for child in self.children:
            if child.is_dir:
                child.sort_children()

    def walk(self) -> Iterator["Node"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, path: str | os.PathLike) -> "Node | None":
        """Locate a node by path inside this subtree (used by the GUI breadcrumb)."""
        wanted = os.path.normcase(os.path.abspath(str(path)))
        for node in self.walk():
            if os.path.normcase(os.path.abspath(str(node.path))) == wanted:
                return node
        return None

    def to_dict(self, depth: int | None = None) -> dict:
        payload: dict = {
            "name": self.name,
            "path": str(self.path),
            "size": self.size,
            "is_dir": self.is_dir,
        }
        if self.is_dir:
            payload["files"] = self.file_count
        if self.error:
            payload["error"] = self.error
        if self.is_dir and self.children and (depth is None or depth > 0):
            next_depth = None if depth is None else depth - 1
            payload["children"] = [child.to_dict(next_depth) for child in self.children]
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "Node":
        node = cls(
            name=data["name"], path=Path(data["path"]), size=data.get("size", 0),
            is_dir=data.get("is_dir", True), file_count=data.get("files", 0),
            error=data.get("error", ""),
        )
        node.children = [cls.from_dict(item) for item in data.get("children", [])]
        return node


@dataclass
class ScanOptions:
    include_hidden: bool = False
    exclude_names: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))


@dataclass
class ScanStats:
    files: int = 0
    dirs: int = 0
    errors: int = 0
    hardlinks: int = 0
    symlinks: int = 0
    bytes: int = 0


@dataclass
class ScanResult:
    root: Node
    stats: ScanStats = field(default_factory=ScanStats)
    elapsed: float = 0.0
    scanned_at: str = ""
    roots: list[Path] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.root.size

    def largest(self, count: int = 20, include_files: bool = True,
                include_dirs: bool = True) -> list[tuple[Node, int]]:
        """Biggest items by size, with their depth below the scanned root."""
        items: list[tuple[Node, int]] = []
        stack: list[tuple[Node, int]] = [(self.root, 0)]
        while stack:
            node, depth = stack.pop()
            if depth > 0:
                if (node.is_dir and include_dirs) or (not node.is_dir and include_files):
                    items.append((node, depth))
            for child in node.children:
                stack.append((child, depth + 1))
        items.sort(key=lambda pair: -pair[0].size)
        return items[:count]

    def categories(self) -> list[tuple[str, int, int]]:
        """[(category, bytes, files)] for the whole tree."""
        sizes: Counter = Counter()
        counts: Counter = Counter()
        for node in self.root.walk():
            if node.is_dir:
                continue
            category = file_category(node.name)
            sizes[category] += node.size
            counts[category] += 1
        return [(name, sizes[name], counts[name])
                for name in sorted(sizes, key=lambda key: -sizes[key])]


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #
def scan(
    root: str | os.PathLike,
    options: ScanOptions | None = None,
    progress: Callable[[str, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    max_entries: int | None = None,
) -> ScanResult:
    """Read-only walk of ``root``. Never modifies the filesystem."""
    options = options or ScanOptions()
    started = time.perf_counter()
    root_path = Path(root).expanduser()
    if not root_path.exists():
        raise FileNotFoundError(f"no such folder: {root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"not a folder: {root_path}")

    stats = ScanStats()
    seen_inodes: set[tuple[int, int]] = set()
    excluded = {name.lower() for name in options.exclude_names}

    def walk(path: Path) -> Node:
        node = Node(name=path.name or str(path), path=path, is_dir=True)
        try:
            entries = list(os.scandir(path))
        except OSError as exc:
            node.error = str(exc)
            stats.errors += 1
            return node

        for entry in entries:
            if cancel and cancel():
                break
            if max_entries and stats.files + stats.dirs >= max_entries:
                break
            try:
                if entry.is_symlink():
                    stats.symlinks += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() in excluded:
                        continue
                    if not options.include_hidden and _is_hidden(entry):
                        continue
                    stats.dirs += 1
                    child = walk(Path(entry.path))
                    node.children.append(child)
                    node.size += child.size
                    node.file_count += child.file_count
                    continue

                if not options.include_hidden and _is_hidden(entry):
                    continue
                # os.stat, not entry.stat(): on Windows DirEntry stat data has
                # st_ino == 0, which would hide every hard link. Symlinks are
                # already skipped above, so following is safe here.
                info = os.stat(entry.path)
                inode = (info.st_dev, info.st_ino)
                if info.st_ino and inode in seen_inodes:
                    stats.hardlinks += 1
                    continue
                if info.st_ino:
                    seen_inodes.add(inode)
                stats.files += 1
                stats.bytes += info.st_size
                node.size += info.st_size
                node.file_count += 1
                node.children.append(Node(
                    name=entry.name, path=Path(entry.path), size=info.st_size,
                    is_dir=False, file_count=1,
                ))
                if progress and stats.files % 500 == 0:
                    progress(entry.name, stats.files)
            except OSError:
                stats.errors += 1
                continue
        return node

    tree = walk(root_path)
    tree.sort_children()
    result = ScanResult(
        root=tree,
        stats=stats,
        elapsed=time.perf_counter() - started,
        scanned_at=datetime.now().isoformat(timespec="seconds"),
        roots=[root_path],
    )
    if progress:
        progress("done", stats.files)
    return result


# --------------------------------------------------------------------------- #
# treemap layout (squarified)
# --------------------------------------------------------------------------- #
def _worst_ratio(row: Sequence[float], side: float) -> float:
    total = sum(row)
    if total <= 0 or side <= 0:
        return float("inf")
    largest, smallest = max(row), min(row)
    if smallest <= 0:
        return float("inf")
    return max(
        (side * side * largest) / (total * total),
        (total * total) / (side * side * smallest),
    )


def squarify(sizes: Sequence[float], width: float, height: float) -> list[tuple[float, float, float, float]]:
    """Lay out areas proportional to ``sizes`` inside a width x height box.

    Returns (x, y, w, h) rectangles in the same order as the input.
    The result is a squarified treemap: items are as close to square as the
    sizes allow, which is what makes a treemap readable.
    """
    if not sizes or width <= 0 or height <= 0:
        return []
    values = [max(float(size), 0.0) for size in sizes]
    total = float(sum(values))
    if total <= 0:
        return []

    scale = (width * height) / total
    areas = [value * scale for value in values]
    rects: list[tuple[float, float, float, float]] = [None] * len(areas)  # type: ignore[list-item]

    def place(indices: list[int], x: float, y: float, w: float, h: float) -> None:
        if not indices:
            return
        if len(indices) == 1:
            rects[indices[0]] = (x, y, w, h)
            return
        side = min(w, h)
        row: list[int] = [indices[0]]
        rest = indices[1:]
        while rest:
            candidate = row + [rest[0]]
            if _worst_ratio([areas[i] for i in candidate], side) <= _worst_ratio(
                [areas[i] for i in row], side
            ):
                row.append(rest.pop(0))
            else:
                break

        row_area = sum(areas[i] for i in row)
        if w >= h:
            thickness = row_area / h if h else 0.0
            offset = 0.0
            for index in row:
                item_height = areas[index] / thickness if thickness else 0.0
                rects[index] = (x, y + offset, thickness, item_height)
                offset += item_height
            place(rest, x + thickness, y, w - thickness, h)
        else:
            thickness = row_area / w if w else 0.0
            offset = 0.0
            for index in row:
                item_width = areas[index] / thickness if thickness else 0.0
                rects[index] = (x + offset, y, item_width, thickness)
                offset += item_width
            place(rest, x, y + thickness, w, h - thickness)

    place(list(range(len(areas))), 0.0, 0.0, float(width), float(height))
    return [rect if rect is not None else (0.0, 0.0, 0.0, 0.0) for rect in rects]


def layout_children(
    node: Node,
    width: float,
    height: float,
    min_fraction: float = 0.004,
    max_items: int = 400,
) -> tuple[list[tuple[Node, tuple[float, float, float, float]]], list[Node]]:
    """Treemap rectangles for one folder level.

    Children smaller than ``min_fraction`` of the area (or beyond ``max_items``)
    are returned separately so the caller can draw one "N smaller items" box
    instead of unreadable slivers.
    """
    children = [child for child in node.children if child.size > 0]
    total = sum(child.size for child in children)
    if total <= 0 or width <= 0 or height <= 0:
        return [], []

    threshold = total * min_fraction
    children.sort(key=lambda child: (-child.size, child.name.lower()))
    big = [child for child in children if child.size >= threshold][:max_items]
    small = [child for child in children if child.size < threshold]
    small += children[max_items:]
    if not big:
        big, small = children, []

    rects = squarify([child.size for child in big], width, height)
    return list(zip(big, rects)), small


def aggregate_node(node: Node, small: Sequence[Node]) -> Node:
    """One pseudo-child standing in for every item too small to draw."""
    return Node(
        name=f"{len(small)} smaller items",
        path=node.path,
        size=sum(child.size for child in small),
        is_dir=False,
        file_count=sum(child.file_count for child in small),
        is_aggregate=True,
    )


def treemap_items(
    node: Node,
    width: float,
    height: float,
    min_fraction: float = 0.004,
    max_items: int = 400,
) -> list[tuple[Node, tuple[float, float, float, float]]]:
    """Everything to draw for one level, small items folded into one box."""
    big, small = layout_children(node, width, height, min_fraction, max_items)
    if not big:
        return []
    if not small:
        return big
    combined = [child for child, _rect in big] + [aggregate_node(node, small)]
    rects = squarify([child.size for child in combined], width, height)
    return list(zip(combined, rects))


# --------------------------------------------------------------------------- #
# exports
# --------------------------------------------------------------------------- #
def export_json(result: ScanResult, path: str | os.PathLike, depth: int | None = None) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "app": APP_NAME,
        "version": VERSION,
        "scanned_at": result.scanned_at,
        "roots": [str(root) for root in result.roots],
        "elapsed_seconds": round(result.elapsed, 3),
        "totals": {
            "bytes": result.stats.bytes,
            "files": result.stats.files,
            "dirs": result.stats.dirs,
            "errors": result.stats.errors,
            "hardlinks_skipped": result.stats.hardlinks,
            "symlinks_skipped": result.stats.symlinks,
        },
        "categories": [
            {"category": name, "bytes": size, "files": count}
            for name, size, count in result.categories()
        ],
        "tree": result.root.to_dict(depth),
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def export_csv(result: ScanResult, path: str | os.PathLike, max_rows: int = 100_000) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        ((node, depth) for node, depth in result.largest(max_rows, True, True)),
        key=lambda pair: -pair[0].size,
    )
    with out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path", "name", "type", "bytes", "size", "depth", "files"])
        for node, depth in rows:
            writer.writerow([
                str(node.path), node.name, "dir" if node.is_dir else "file",
                node.size, human_size(node.size), depth, node.file_count,
            ])
    return out


def print_tree(result: ScanResult, max_depth: int = 3, limit: int = 12,
               min_size: int = 0) -> Iterator[str]:
    """Yield an indented tree of the biggest children, biggest first."""

    def walk(node: Node, depth: int, prefix: str) -> Iterator[str]:
        children = [child for child in node.children if child.size >= min_size]
        for index, child in enumerate(children[:limit]):
            last = index == len(children[:limit]) - 1
            branch = "└── " if last else "├── "
            share = (child.size / result.size * 100) if result.size else 0
            yield (f"{prefix}{branch}{human_size(child.size):>9}  "
                   f"{share:5.1f}%  {child.name}{os.sep if child.is_dir else ''}")
            if child.is_dir and depth > 1:
                yield from walk(child, depth - 1, prefix + ("    " if last else "│   "))

    yield f"{human_size(result.size):>9}  100.0%  {result.root.path}"
    yield from walk(result.root, max_depth, "")


def category_legend(result: ScanResult) -> list[tuple[str, int, float]]:
    total = result.stats.bytes or 1
    return [(name, size, size * 100 / total) for name, size, _ in result.categories()]
