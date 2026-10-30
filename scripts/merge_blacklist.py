"""Consolidate local keyword files without changing their matching semantics."""

from pathlib import Path

from forum_archive.config import normalize_title


def merge_blacklist(directory: Path) -> int:
    target = directory / "blacklist.txt"
    submissions = sorted(path for path in directory.glob("*.txt") if path != target)
    paths = ([target] if target.exists() else []) + submissions
    seen = set()
    keywords = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"词库必须为普通文件：{path}")
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            word = line.strip()
            normalized = normalize_title(word)
            if normalized and normalized not in seen:
                seen.add(normalized)
                keywords.append(word)
    if not paths:
        return 0
    content = "\n".join(keywords) + ("\n" if keywords else "")
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        temporary = directory / "blacklist.txt.tmp"
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
    for path in submissions:
        path.unlink()
    return len(submissions)


if __name__ == "__main__":
    count = merge_blacklist(Path(__file__).resolve().parents[1] / "blacklist")
    print(f"已将 {count} 个词库文件合并到 blacklist/blacklist.txt")
