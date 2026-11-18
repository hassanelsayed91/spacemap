# Changelog

All notable changes to SpaceMap are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-18

First public release - day 3 of "30 Windows tools in 30 days".

### Added
- Read-only disk scanner built on `os.scandir`, with per-folder totals and file counts.
- Squarified treemap layout (Bruls, Huizing & van Wijk) with unit-tested rectangles: inside the box, no overlaps, areas proportional to sizes.
- Desktop app (Tkinter): drill-down treemap, breadcrumb, `Esc` to go up, hover details, right-click Explorer/copy actions, "biggest here" list and a category breakdown for the current folder.
- Folders painted by their dominant file type, so a folder's purpose is visible at a glance.
- "N smaller items" aggregation keeps large folders readable (no slivers).
- Hard link awareness (one physical file counted once) and symlink skipping.
- CLI: `scan` with `--depth`, `--limit`, `--min-size`, `--top`, `--json`, `--csv`; plus `gui`.
- JSON and CSV reports.
- 66 unit tests, GitHub Actions CI on Windows and Linux (Python 3.10 and 3.13).
- MIT license, bilingual documentation (English + Arabic).
