"""Tests for the SpaceMap core. Run with: python -m pytest -q

The layout tests are the important ones: a treemap that draws outside its box,
overlaps, or ignores the sizes is worse than no treemap at all.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from spacemap import core


def area(rect: tuple[float, float, float, float]) -> float:
    return rect[2] * rect[3]


def overlap(a: tuple[float, float, float, float],
            b: tuple[float, float, float, float]) -> float:
    x = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    y = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return x * y


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "root"
        self.root.mkdir()
        self.write("big.bin", 1000)
        self.write("photos/a.jpg", 500)
        self.write("photos/b.png", 300)
        self.write("docs/note.txt", 100)
        (self.root / "empty").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, size: int, data: bytes | None = None) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if data is not None else b"x" * size)
        return path

    def scan(self, **kwargs) -> core.ScanResult:
        return core.scan(self.root, core.ScanOptions(**kwargs))


class ScanTests(BaseCase):
    def test_total_size_is_the_sum_of_every_file(self) -> None:
        result = self.scan()
        self.assertEqual(result.size, 1900)
        self.assertEqual(result.stats.bytes, 1900)

    def test_file_and_folder_counts(self) -> None:
        result = self.scan()
        self.assertEqual(result.stats.files, 4)
        self.assertEqual(result.stats.dirs, 3)          # photos, docs, empty
        self.assertEqual(result.root.file_count, 4)

    def test_folder_size_includes_its_subtree(self) -> None:
        result = self.scan()
        photos = next(child for child in result.root.children if child.name == "photos")
        self.assertEqual(photos.size, 800)
        self.assertEqual(photos.file_count, 2)
        self.assertEqual(len(photos.children), 2)

    def test_children_are_sorted_biggest_first(self) -> None:
        result = self.scan()
        sizes = [child.size for child in result.root.children]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(result.root.children[0].name, "big.bin")

    def test_empty_folder_is_kept_with_zero_size(self) -> None:
        result = self.scan()
        empty = next(child for child in result.root.children if child.name == "empty")
        self.assertTrue(empty.is_dir)
        self.assertEqual(empty.size, 0)
        self.assertEqual(empty.children, [])

    def test_missing_folder_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            core.scan(self.root / "nope")

    def test_a_file_is_not_a_folder(self) -> None:
        with self.assertRaises(NotADirectoryError):
            core.scan(self.root / "big.bin")

    def test_excluded_folder_name_is_skipped(self) -> None:
        self.write("node_modules/big.js", 5000)
        result = self.scan()
        names = [child.name for child in result.root.children]
        self.assertNotIn("node_modules", names)
        self.assertEqual(result.size, 1900)

    def test_dot_files_are_skipped_by_default(self) -> None:
        self.write(".secret", 4000)
        result = self.scan()
        self.assertEqual(result.size, 1900)
        self.assertEqual(result.stats.files, 4)

    def test_hidden_files_can_be_included(self) -> None:
        self.write(".secret", 4000)
        result = self.scan(include_hidden=True)
        self.assertEqual(result.size, 5900)
        self.assertIn(".secret", [child.name for child in result.root.children])

    def test_hidden_folders_are_skipped(self) -> None:
        self.write(".cache/blob.bin", 7000)
        result = self.scan()
        self.assertNotIn(".cache", [child.name for child in result.root.children])
        self.assertEqual(self.scan(include_hidden=True).size, 8900)

    def test_symlink_is_not_followed(self) -> None:
        target = self.root / "photos"
        link = self.root / "photos-link"
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlinks need privileges on this machine")
        result = self.scan()
        self.assertEqual(result.size, 1900)
        self.assertEqual(result.stats.symlinks, 1)

    def test_hard_link_is_counted_once(self) -> None:
        original = self.write("hard-a.bin", 2500)
        link = self.root / "hard-b.bin"
        try:
            os.link(original, link)
        except (OSError, NotImplementedError):
            self.skipTest("hard links are not supported here")
        result = self.scan()
        self.assertEqual(result.size, 1900 + 2500)
        self.assertEqual(result.stats.hardlinks, 1)

    def test_scan_never_changes_a_file(self) -> None:
        before = {path: (path.stat().st_size, path.stat().st_mtime_ns)
                  for path in self.root.rglob("*")}
        self.scan()
        after = {path: (path.stat().st_size, path.stat().st_mtime_ns)
                 for path in self.root.rglob("*")}
        self.assertEqual(before, after)

    def test_scan_does_not_create_a_journal_or_config(self) -> None:
        base = Path(self._tmp.name)
        before = {path.name for path in base.iterdir()}
        self.scan()
        after = {path.name for path in base.iterdir()}
        self.assertEqual(before, after)

    def test_progress_callback_reports_files(self) -> None:
        calls: list[int] = []
        core.scan(self.root, progress=lambda name, files: calls.append(files))
        self.assertTrue(calls)
        self.assertEqual(calls[-1], 4)   # "done" call

    def test_cancel_stops_the_walk(self) -> None:
        result = core.scan(self.root, cancel=lambda: True)
        self.assertEqual(result.stats.files, 0)
        self.assertEqual(result.size, 0)

    def test_max_entries_caps_the_scan(self) -> None:
        result = core.scan(self.root, max_entries=2)
        self.assertLessEqual(result.stats.files + result.stats.dirs, 2)
        self.assertLess(result.size, 1900)

    def test_unreadable_folder_is_counted_not_fatal(self) -> None:
        if os.name == "nt":
            self.skipTest("chmod does not block reads on Windows")
        locked = self.root / "locked"
        locked.mkdir()
        (locked / "file.bin").write_bytes(b"x" * 10)
        locked.chmod(0o000)
        try:
            result = self.scan()
        finally:
            locked.chmod(0o755)
        self.assertEqual(result.stats.errors, 1)
        self.assertEqual(result.size, 1900)


class ModelTests(BaseCase):
    def test_categories_add_up_to_the_total(self) -> None:
        result = self.scan()
        rows = result.categories()
        names = [name for name, _size, _count in rows]
        self.assertIn("Images", names)
        self.assertIn("Documents", names)
        self.assertEqual(sum(size for _n, size, _c in rows), result.size)
        self.assertEqual({name: count for name, _s, count in rows}["Images"], 2)

    def test_categories_are_sorted_by_size(self) -> None:
        sizes = [size for _name, size, _count in self.scan().categories()]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_largest_includes_files_and_folders(self) -> None:
        items = self.scan().largest(10)
        names = [node.name for node, _depth in items]
        self.assertIn("big.bin", names)
        self.assertIn("photos", names)
        sizes = [node.size for node, _depth in items]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_largest_respects_the_count(self) -> None:
        self.assertEqual(len(self.scan().largest(2)), 2)

    def test_largest_can_exclude_files_or_folders(self) -> None:
        result = self.scan()
        only_files = result.largest(20, include_files=True, include_dirs=False)
        only_dirs = result.largest(20, include_files=False, include_dirs=True)
        self.assertTrue(all(not node.is_dir for node, _ in only_files))
        self.assertTrue(all(node.is_dir for node, _ in only_dirs))
        self.assertNotIn("root", [node.name for node, _ in only_dirs])

    def test_largest_reports_depth(self) -> None:
        depths = {node.name: depth for node, depth in self.scan().largest(20)}
        self.assertEqual(depths["photos"], 1)
        self.assertEqual(depths["a.jpg"], 2)

    def test_find_returns_a_node_by_path(self) -> None:
        result = self.scan()
        target = self.root / "photos" / "a.jpg"
        found = result.root.find(target)
        self.assertIsNotNone(found)
        self.assertEqual(found.size, 500)
        self.assertIsNone(result.root.find(self.root / "nothing-here"))

    def test_node_dict_roundtrip(self) -> None:
        result = self.scan()
        payload = result.root.to_dict()
        copy = core.Node.from_dict(payload)
        self.assertEqual(copy.name, result.root.name)
        self.assertEqual(copy.size, result.root.size)
        self.assertEqual(len(copy.children), len(result.root.children))

    def test_to_dict_depth_limits_children(self) -> None:
        payload = self.scan().root.to_dict(depth=1)
        photos = next(item for item in payload["children"] if item["name"] == "photos")
        self.assertNotIn("children", photos)

    def test_dominant_category_of_a_folder(self) -> None:
        result = self.scan()
        photos = next(child for child in result.root.children if child.name == "photos")
        docs = next(child for child in result.root.children if child.name == "docs")
        self.assertEqual(core.dominant_category(photos), "Images")
        self.assertEqual(core.dominant_category(docs), "Documents")

    def test_dominant_category_of_an_empty_folder(self) -> None:
        empty = core.Node(name="empty", path=self.root / "empty")
        self.assertEqual(core.dominant_category(empty), "Folder")

    def test_file_category_is_case_insensitive(self) -> None:
        self.assertEqual(core.file_category("PHOTO.JPG"), "Images")
        self.assertEqual(core.file_category("archive.tar.gz"), "Archives")
        self.assertEqual(core.file_category("mystery.xyz"), "Other")

    def test_node_color_differs_between_types(self) -> None:
        image = core.Node(name="a.jpg", path=Path("a.jpg"), is_dir=False)
        video = core.Node(name="a.mp4", path=Path("a.mp4"), is_dir=False)
        folder = core.Node(name="stuff", path=Path("stuff"), is_dir=True)
        self.assertNotEqual(core.node_color(image), core.node_color(video))
        self.assertNotEqual(core.node_color(image), core.node_color(folder))

    def test_lighten_moves_towards_white(self) -> None:
        self.assertEqual(core.lighten("#000000", 0.0), "#000000")
        self.assertEqual(core.lighten("#000000", 1.0), "#ffffff")
        self.assertEqual(core.lighten("bogus"), "bogus")


class SquarifyTests(unittest.TestCase):
    def test_one_item_fills_the_box(self) -> None:
        rects = core.squarify([5], 200, 100)
        self.assertEqual(len(rects), 1)
        self.assertAlmostEqual(area(rects[0]), 200 * 100, places=6)

    def test_returns_one_rect_per_size(self) -> None:
        rects = core.squarify([5, 3, 2], 300, 200)
        self.assertEqual(len(rects), 3)

    def test_areas_are_proportional_to_sizes(self) -> None:
        sizes = [6, 6, 4, 3, 2, 2, 1]
        width, height = 400, 250
        rects = core.squarify(sizes, width, height)
        total = sum(sizes)
        for size, rect in zip(sizes, rects):
            expected = width * height * size / total
            self.assertAlmostEqual(area(rect), expected, places=5,
                                   msg=f"size {size} got area {area(rect)}")

    def test_every_rect_stays_inside_the_box(self) -> None:
        rects = core.squarify([9, 7, 5, 3, 2, 2, 1, 1], 640, 360)
        for x, y, w, h in rects:
            self.assertGreaterEqual(x, -1e-9)
            self.assertGreaterEqual(y, -1e-9)
            self.assertLessEqual(x + w, 640 + 1e-6)
            self.assertLessEqual(y + h, 360 + 1e-6)
            self.assertGreaterEqual(w, 0)
            self.assertGreaterEqual(h, 0)

    def test_rects_do_not_overlap(self) -> None:
        rects = core.squarify([8, 6, 5, 4, 3, 2, 1, 1, 1], 500, 300)
        for index, first in enumerate(rects):
            for second in rects[index + 1:]:
                self.assertLess(overlap(first, second), 1e-6)

    def test_rects_cover_the_whole_box(self) -> None:
        rects = core.squarify([5, 4, 3, 2, 1], 300, 300)
        self.assertAlmostEqual(sum(area(rect) for rect in rects), 300 * 300, places=4)

    def test_squarified_shapes_are_reasonably_square(self) -> None:
        rects = core.squarify([10, 10, 10, 10], 400, 400)
        for _x, _y, width, height in rects:
            ratio = max(width, height) / max(1e-9, min(width, height))
            self.assertLess(ratio, 3.0)

    def test_empty_input_returns_nothing(self) -> None:
        self.assertEqual(core.squarify([], 100, 100), [])

    def test_zero_area_box_returns_nothing(self) -> None:
        self.assertEqual(core.squarify([1, 2], 0, 100), [])
        self.assertEqual(core.squarify([1, 2], 100, 0), [])

    def test_zero_total_returns_nothing(self) -> None:
        self.assertEqual(core.squarify([0, 0], 100, 100), [])

    def test_zero_sized_item_does_not_break_the_layout(self) -> None:
        rects = core.squarify([5, 0, 5], 200, 100)
        self.assertEqual(len(rects), 3)
        self.assertAlmostEqual(area(rects[1]), 0, places=6)
        self.assertAlmostEqual(area(rects[0]), 200 * 100 * 5 / 10, places=4)

    def test_negative_size_is_clamped(self) -> None:
        rects = core.squarify([5, -5], 100, 100)
        self.assertAlmostEqual(area(rects[1]), 0, places=6)
        self.assertAlmostEqual(area(rects[0]), 100 * 100, places=4)


class LayoutTests(BaseCase):
    def test_layout_covers_one_folder_level(self) -> None:
        result = self.scan()
        items, small = core.layout_children(result.root, 300, 200)
        self.assertEqual(small, [])
        non_empty = [child for child in result.root.children if child.size > 0]
        self.assertEqual(len(items), len(non_empty))
        self.assertAlmostEqual(sum(area(rect) for _node, rect in items),
                               300 * 200, places=4)

    def test_small_children_are_split_out(self) -> None:
        node = core.Node(name="root", path=self.root)
        node.children = [
            core.Node(name="huge", path=Path("huge"), size=100_000),
            core.Node(name="tiny", path=Path("tiny"), size=10),
            core.Node(name="speck", path=Path("speck"), size=5),
        ]
        items, small = core.layout_children(node, 200, 200, min_fraction=0.01)
        self.assertEqual([child.name for child, _ in items], ["huge"])
        self.assertEqual(sorted(child.name for child in small), ["speck", "tiny"])

    def test_everything_is_drawn_when_all_are_tiny(self) -> None:
        node = core.Node(name="root", path=self.root)
        node.children = [core.Node(name=f"f{index}", path=Path(f"f{index}"), size=1)
                         for index in range(4)]
        items, small = core.layout_children(node, 100, 100, min_fraction=10.0)
        self.assertEqual(len(items), 4)
        self.assertEqual(small, [])

    def test_max_items_caps_the_drawn_rectangles(self) -> None:
        node = core.Node(name="root", path=self.root)
        node.children = [core.Node(name=f"f{index}", path=Path(f"f{index}"), size=100)
                         for index in range(20)]
        items, small = core.layout_children(node, 300, 300, min_fraction=0, max_items=5)
        self.assertEqual(len(items), 5)
        self.assertEqual(len(small), 15)

    def test_empty_folder_has_no_rectangles(self) -> None:
        node = core.Node(name="root", path=self.root)
        self.assertEqual(core.layout_children(node, 100, 100), ([], []))

    def test_zero_size_children_are_ignored(self) -> None:
        node = core.Node(name="root", path=self.root)
        node.children = [
            core.Node(name="a", path=Path("a"), size=10),
            core.Node(name="empty", path=Path("empty"), size=0),
        ]
        items, small = core.layout_children(node, 100, 100)
        self.assertEqual([child.name for child, _ in items], ["a"])
        self.assertEqual(small, [])

    def test_aggregate_node_keeps_the_total(self) -> None:
        node = core.Node(name="root", path=self.root)
        node.children = [core.Node(name="big", path=Path("big"), size=10_000)]
        node.children += [core.Node(name=f"t{index}", path=Path(f"t{index}"), size=1)
                          for index in range(30)]
        items = core.treemap_items(node, 300, 300, min_fraction=0.01)
        self.assertEqual(items[-1][0].name, "30 smaller items")
        self.assertTrue(items[-1][0].is_aggregate)
        self.assertEqual(sum(child.size for child, _ in items), 10_030)

    def test_no_aggregate_when_everything_is_big(self) -> None:
        result = self.scan()
        items = core.treemap_items(result.root, 300, 300, min_fraction=0.001)
        self.assertFalse(any(child.is_aggregate for child, _ in items))

    def test_aggregate_rectangles_stay_inside_the_box(self) -> None:
        result = self.scan()
        items = core.treemap_items(result.root, 250, 180, min_fraction=0.2)
        for _child, (x, y, width, height) in items:
            self.assertLessEqual(x + width, 250 + 1e-6)
            self.assertLessEqual(y + height, 180 + 1e-6)


class ExportTests(BaseCase):
    def test_export_json_structure(self) -> None:
        result = self.scan()
        out = core.export_json(result, self.root / "out" / "map.json")
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["app"], "SpaceMap")
        self.assertEqual(data["totals"]["bytes"], 1900)
        self.assertEqual(data["totals"]["files"], 4)
        self.assertEqual(data["tree"]["name"], "root")
        self.assertTrue(data["categories"])

    def test_export_json_can_limit_depth(self) -> None:
        out = core.export_json(self.scan(), self.root / "flat.json", depth=1)
        data = json.loads(out.read_text(encoding="utf-8"))
        photos = next(item for item in data["tree"]["children"] if item["name"] == "photos")
        self.assertNotIn("children", photos)

    def test_export_csv_has_a_row_per_item(self) -> None:
        result = self.scan()
        out = core.export_csv(result, self.root / "map.csv")
        lines = out.read_text(encoding="utf-8-sig").strip().splitlines()
        self.assertEqual(lines[0].split(",")[:3], ["path", "name", "type"])
        self.assertEqual(len(lines), 1 + 4 + 3)   # header + 4 files + 3 folders

    def test_export_csv_marks_folders(self) -> None:
        out = core.export_csv(self.scan(), self.root / "map.csv")
        self.assertIn(",dir,", out.read_text(encoding="utf-8-sig"))

    def test_print_tree_starts_with_the_root(self) -> None:
        lines = list(core.print_tree(self.scan()))
        self.assertIn("100.0%", lines[0])
        self.assertIn(str(self.root), lines[0])
        self.assertTrue(any("big.bin" in line for line in lines))

    def test_print_tree_respects_limit_and_depth(self) -> None:
        lines = list(core.print_tree(self.scan(), max_depth=1, limit=2))
        self.assertEqual(len(lines), 3)   # header + 2 children, no nesting
        self.assertTrue(any("photos" in line for line in lines))

    def test_print_tree_hides_small_items(self) -> None:
        lines = list(core.print_tree(self.scan(), min_size=400))
        self.assertFalse(any("note.txt" in line for line in lines))
        self.assertTrue(any("big.bin" in line for line in lines))

    def test_category_legend_percentages_add_up(self) -> None:
        legend = core.category_legend(self.scan())
        self.assertAlmostEqual(sum(percent for _n, _s, percent in legend), 100.0, places=6)

    def test_category_legend_of_an_empty_folder(self) -> None:
        empty = Path(self._tmp.name) / "empty-root"
        empty.mkdir()
        self.assertEqual(core.category_legend(core.scan(empty)), [])


class HelperTests(unittest.TestCase):
    def test_human_size(self) -> None:
        self.assertEqual(core.human_size(0), "0 B")
        self.assertEqual(core.human_size(999), "999 B")
        self.assertEqual(core.human_size(2048), "2.0 KB")
        self.assertEqual(core.human_size(1024 ** 3), "1.0 GB")

    def test_parse_size(self) -> None:
        self.assertEqual(core.parse_size("1024"), 1024)
        self.assertEqual(core.parse_size("1 KB"), 1024)
        self.assertEqual(core.parse_size("2gb"), 2 * 1024 ** 3)
        self.assertEqual(core.parse_size("1.5 MB"), int(1.5 * 1024 ** 2))
        self.assertEqual(core.parse_size(77), 77)
        with self.assertRaises(ValueError):
            core.parse_size("banana")
        with self.assertRaises(ValueError):
            core.parse_size("10 lightyears")

    def test_version_is_exposed(self) -> None:
        self.assertEqual(core.VERSION, "1.0.0")
        self.assertIn("spacemap", core.PROJECT_URL)


if __name__ == "__main__":
    unittest.main()
