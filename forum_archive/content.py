import re

from bs4 import BeautifulSoup


def clean_display_text(content: BeautifulSoup) -> bool:
    changed = False
    likes = re.search(r"赞\s*[(（]\s*\d+\s*[)）]\s*$", content.get_text())
    if likes:
        offset = 0
        for node in list(content.strings):
            text = str(node)
            node.replace_with(text[:max(0, likes.start() - offset)])
            offset += len(text)
        changed = True
    if "防水图" in content.get_text():
        # Use text offsets so a marker split across tags is removed too.
        removed = {index for match in re.finditer("防水图", content.get_text())
                   for index in range(*match.span())}
        offset = 0
        for node in list(content.strings):
            text = str(node)
            node.replace_with("".join(char for index, char in enumerate(text, offset) if index not in removed))
            offset += len(text)
        changed = True
    return changed
