"""Command line interface for SpaceMap."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core
from .core import ScanOptions

BANNER = f"SpaceMap {core.VERSION} - see where the disk space went"


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def _parse_size(text: str) -> int:
    try:
        return core.parse_size(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _default_root() -> Path:
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        return downloads
    return Path.home()


def _progress():
    if not sys.stdout.isatty():
        return None, None

    def show(name: str, files: int) -> None:
        sys.stdout.write(f"\r  scanning... {files:>7} files  {name[:40]:<40}")
        sys.stdout.flush()

    def finish() -> None:
        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()

    return show, finish


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser() if args.path else _default_root()
    options = ScanOptions(
        include_hidden=args.include_hidden,
        exclude_names=list(core.DEFAULT_EXCLUDE) + list(args.exclude or []),
    )
    print(f"\n{BANNER}\n  scanning: {root}\n")
    progress, finish = _progress()
    try:
        result = core.scan(root, options, progress=progress)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n  cancelled.\n")
        return 130
    finally:
        if finish:
            finish()

    stats = result.stats
    print(f"  {core.human_size(result.size)} in {stats.files:,} files and {stats.dirs:,} folders "
          f"({result.elapsed:.1f}s)")
    if stats.errors:
        print(f"  {stats.errors} item(s) could not be read (permissions)")
    if stats.hardlinks:
        print(f"  {stats.hardlinks} hard link(s) counted once")
    if stats.symlinks:
        print(f"  {stats.symlinks} symlink(s) skipped")

    print(f"\n  Largest items")
    for line in core.print_tree(result, max_depth=args.depth, limit=args.limit,
                                min_size=args.min_size):
        print(f"  {line}")

    legend = core.category_legend(result)
    if legend:
        print("\n  By type")
        width = max(len(name) for name, _, _ in legend)
        for name, size, percent in legend[:10]:
            bar = "█" * max(1, int(percent / 3))
            print(f"  {name.ljust(width)}  {core.human_size(size):>9}  {percent:5.1f}%  {bar}")

    if args.json:
        print(f"\n  JSON report: {core.export_json(result, args.json, depth=None)}")
    if args.csv:
        print(f"  CSV report:  {core.export_csv(result, args.csv)}")

    if args.top:
        print(f"\n  Top {args.top} files")
        files = result.largest(args.top, include_files=True, include_dirs=False)
        for node, _depth in files:
            print(f"  {core.human_size(node.size):>9}  {node.path}")

    print("\n  Nothing was modified - SpaceMap never writes to your files.\n")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .gui import main as gui_main
    except ImportError as exc:
        print(f"Tkinter is not available: {exc}", file=sys.stderr)
        return 1
    gui_main(args.path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spacemap",
        description="Visualize disk usage as a treemap. Read-only by design.",
    )
    parser.add_argument("--version", action="version", version=f"SpaceMap {core.VERSION}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("path", nargs="?", help="folder to inspect (default: Downloads)")
    common.add_argument("--include-hidden", action="store_true", help="count hidden/system files")
    common.add_argument("--exclude", action="append", metavar="NAME",
                        help="extra folder name to skip (repeatable)")

    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", parents=[common], help="measure a folder and print the biggest items")
    scan.add_argument("-d", "--depth", type=int, default=3, help="how deep to print the tree")
    scan.add_argument("-l", "--limit", type=int, default=12, help="children per folder")
    scan.add_argument("--min-size", type=_parse_size, default=0, metavar="SIZE",
                      help="hide items smaller than this, e.g. 100MB")
    scan.add_argument("--top", type=int, default=0, metavar="N", help="also list the N biggest files")
    scan.add_argument("--json", metavar="FILE", help="write a JSON report")
    scan.add_argument("--csv", metavar="FILE", help="write a CSV report")
    scan.set_defaults(func=cmd_scan)

    gui = sub.add_parser("gui", parents=[common], help="open the treemap window")
    gui.set_defaults(func=cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n  cancelled.\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
