(() => {
  "use strict";

  const menu = document.getElementById("keyword-menu");
  const main = document.getElementById("main");
  if (!menu || !main) return;

  const submit = menu.querySelector(".keyword-submit");
  const keywords = JSON.parse(menu.dataset.keywords);
  let selectedText = "";
  let selectionRect = null;

  const normalize = value => value.normalize("NFKC").toLowerCase().replace(/[\s\p{Cf}]/gu, "");

  function hide() {
    selectedText = "";
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
    if (!normalized || Array.from(text).length > 80
        || keywords.some(keyword => normalized.includes(keyword))) {
      return;
    }

    selectedText = text;
    selectionRect = range.getBoundingClientRect();
    submit.href = `https://github.com/${menu.dataset.repository}/edit/${encodeURIComponent(menu.dataset.branch)}/blacklist/keywords.txt`;
    submit.hidden = false;
    menu.hidden = false;
    position();
  }

  submit.addEventListener("click", event => {
    const text = selectedText;
    if (!text) {
      event.preventDefault();
      return;
    }
    // Synchronous copy works on public HTTP too, before the link opens a tab.
    const input = document.createElement("textarea");
    input.value = text;
    input.readOnly = true;
    input.style.position = "fixed";
    input.style.top = "0";
    input.style.left = "0";
    input.style.opacity = "0";
    document.body.append(input);
    input.focus({preventScroll: true});
    input.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch {
      copied = false;
    } finally {
      input.remove();
    }
    if (!copied) {
      window.prompt("请手动复制下方关键词；关闭后在 GitHub 文件末尾另起一行粘贴，并创建 PR。", text);
    }
  });

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
