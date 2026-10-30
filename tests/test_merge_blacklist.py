from pathlib import Path
import tempfile
import unittest

from scripts.merge_blacklist import merge_blacklist


class MergeBlacklistTests(unittest.TestCase):
    def test_merge_preserves_keywords_deduplicates_and_consumes_submissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "blacklist.txt"
            target.write_text("原有词\nＡＢＣ\n带,标点\n", encoding="utf-8")
            (directory / "keywords.txt").write_text("\ufeff旧词\n原有词\n", encoding="utf-8")
            (directory / "block-one.txt").write_text("abc\n新 词\n", encoding="utf-8")
            (directory / "block-two.txt").write_text("新词\n特殊$`词\n\n", encoding="utf-8")
            (directory / "README.md").write_text("保留说明", encoding="utf-8")
            nested = directory / "nested"
            nested.mkdir()
            (nested / "keep.txt").write_text("保留文件", encoding="utf-8")
            self.assertEqual(merge_blacklist(directory), 3)
            self.assertEqual(target.read_text(encoding="utf-8"), "原有词\nＡＢＣ\n带,标点\n新 词\n特殊$`词\n旧词\n")
            self.assertEqual(list(directory.glob("*.txt")), [target])
            self.assertEqual((directory / "README.md").read_text(encoding="utf-8"), "保留说明")
            self.assertTrue((nested / "keep.txt").is_file())
            self.assertEqual(merge_blacklist(directory), 0)

    def test_first_submission_creates_canonical_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "block-one.txt").write_text("关键词\n", encoding="utf-8")
            self.assertEqual(merge_blacklist(directory), 1)
            self.assertEqual((directory / "blacklist.txt").read_text(encoding="utf-8"), "关键词\n")

    def test_bad_input_does_not_partially_merge_or_delete_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "blacklist.txt"
            target.write_text("原词\n", encoding="utf-8")
            (directory / "block-one.txt").write_text("新词\n", encoding="utf-8")
            (directory / "block-two.txt").write_bytes(b"\xff")
            with self.assertRaises(UnicodeDecodeError):
                merge_blacklist(directory)
            self.assertEqual(target.read_text(encoding="utf-8"), "原词\n")
            self.assertTrue((directory / "block-one.txt").exists())


if __name__ == "__main__":
    unittest.main()
