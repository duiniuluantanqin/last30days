(() => {
  "use strict";

  const menu = document.getElementById("keyword-menu");
  const main = document.getElementById("main");
  if (!menu || !main) return;

  const submit = menu.querySelector(".keyword-submit");
  const keywords = JSON.parse(menu.dataset.keywords);
  let selectionRect = null;

  const normalize = value => value.normalize("NFKC").toLowerCase().replace(/[\s\p{Cf}]/gu, "");

  function hide() {
    menu.hidden = true;
    submit.hidden = true;
    submit.removeAttribute("href");
  }

  function position() {
    const bounds = menu.getBoundingClientRect();
    const left = Math.max(12, Math.min(selectionRect.left + selectionRect.width / 2 - bounds.width / 2, innerWidth - bounds.width - 12));
    const below = selectionRect.bottom + 10;
    const top = below + bounds.height <= innerHeight - 12 ? below : Math.max(12, selectionRect.top - bounds.height - 10);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  function showSelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount !== 1) {
      hide();
      return;
    }
    const range = selection.getRangeAt(0);
    if (!main.contains(range.startContainer) || !main.contains(range.endContainer)) {
      hide();
      return;
    }
    // A multi-line selection represents one keyword, never several file entries.
    const text = selection.toString().trim().replace(/\s+/gu, " ");
    const normalized = normalize(text);
    hide();
    if (!normalized || Array.from(text).length > 80 || /[\p{C}\p{Zl}\p{Zp}]/u.test(text)
        || keywords.some(keyword => normalized.includes(keyword))) {
      return;
    }

    selectionRect = range.getBoundingClientRect();
    const query = new URLSearchParams({
      title: `[Block] ${text}`,
      body: `<!-- forum-archive-block:v1 -->\n${JSON.stringify({keyword: text})}`,
    });
    submit.href = `https://github.com/${menu.dataset.repository}/issues/new?${query}`;
    submit.hidden = false;
    menu.hidden = false;
    position();
  }

  // Keep the selection while clicking the floating action.
  menu.addEventListener("pointerdown", event => {
    if (event.button === 0) event.preventDefault();
  });
  document.addEventListener("pointerup", event => {
    if (!menu.contains(event.target)) showSelection();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") hide();
  });
  document.addEventListener("keyup", event => {
    if (event.key === "Shift" || event.key.startsWith("Arrow")) showSelection();
  });
  document.addEventListener("selectionchange", () => {
    if (window.getSelection()?.isCollapsed) hide();
  });
  document.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);
})();
