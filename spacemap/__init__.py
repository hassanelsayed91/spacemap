"""SpaceMap - see where your disk space went, as a treemap. Read-only by design."""

from .core import (
    VERSION,
    Node,
    ScanOptions,
    ScanResult,
    ScanStats,
    export_csv,
    export_json,
    human_size,
    layout_children,
    parse_size,
    scan,
    squarify,
    treemap_items,
)

__all__ = [
    "VERSION",
    "Node",
    "ScanOptions",
    "ScanResult",
    "ScanStats",
    "scan",
    "squarify",
    "layout_children",
    "treemap_items",
    "export_json",
    "export_csv",
    "human_size",
    "parse_size",
]
__version__ = VERSION
