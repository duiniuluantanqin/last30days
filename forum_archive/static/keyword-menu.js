(() => {
  "use strict";

  const menu = document.getElementById("keyword-menu");
  const main = document.getElementById("main");
  if (!menu || !main) return;

  const submit = menu.querySelector(".keyword-submit");
  const keywords = JSON.parse(menu.dataset.keywords);
  let generation = 0;
  let selectionRect = null;

  const normalize = value => value.normalize("NFKC").toLowerCase().replace(/[\s\p{Cf}]/gu, "");

  function hide() {
    generation += 1;
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

  async function showSelection() {
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
        || keywords.some(keyword => normalized.includes(keyword)) || !window.crypto?.subtle) {
      return;
    }

    const current = ++generation;
    selectionRect = range.getBoundingClientRect();
    try {
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(normalized));
      if (current !== generation) return;
      const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
      const url = new URL(`https://github.com/${menu.dataset.repository}/new/${encodeURIComponent(menu.dataset.branch)}`);
      url.searchParams.set("filename", `blacklist/${hash}.txt`);
      url.searchParams.set("value", `${text}\n`);
      url.searchParams.set("message", `filter: add keyword ${text}`);
      url.searchParams.set("description", `建议将“${text}”加入标题黑名单。\n来源页面：${location.origin}${location.pathname}\n\n请创建新分支并提交 PR；合并后由下一轮采集发布生效。`);
      // GitHub's editor still requires the user to confirm the branch and PR.
      url.searchParams.set("quick_pull", "1");
      submit.href = url.href;
      submit.hidden = false;
      menu.hidden = false;
      position();
    } catch {
      if (current !== generation) return;
      hide();
    }
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
