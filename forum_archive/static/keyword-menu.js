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

  async function keywordHash(text) {
    const bytes = new TextEncoder().encode(text);
    if (window.crypto?.subtle) {
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
    }

    // HTTP pages lack SubtleCrypto. SHA-256 here only creates a stable filename.
    const constants = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
      0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
      0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];
    const state = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const view = new DataView(padded.buffer);
    const bitLength = bytes.length * 8;
    view.setUint32(padded.length - 8, Math.floor(bitLength / 0x100000000));
    view.setUint32(padded.length - 4, bitLength >>> 0);
    const words = new Uint32Array(64);
    const rotate = (word, count) => (word >>> count) | (word << (32 - count));

    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let i = 0; i < 16; i += 1) words[i] = view.getUint32(offset + i * 4);
      for (let i = 16; i < 64; i += 1) {
        const first = rotate(words[i - 15], 7) ^ rotate(words[i - 15], 18) ^ (words[i - 15] >>> 3);
        const second = rotate(words[i - 2], 17) ^ rotate(words[i - 2], 19) ^ (words[i - 2] >>> 10);
        words[i] = (words[i - 16] + first + words[i - 7] + second) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = state;
      for (let i = 0; i < 64; i += 1) {
        const first = (h + (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)) + ((e & f) ^ (~e & g)) + constants[i] + words[i]) >>> 0;
        const second = ((rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
        [a, b, c, d, e, f, g, h] = [(first + second) >>> 0, a, b, c, (d + first) >>> 0, e, f, g];
      }
      [a, b, c, d, e, f, g, h].forEach((word, i) => { state[i] = (state[i] + word) >>> 0; });
    }
    return Array.from(state, word => word.toString(16).padStart(8, "0")).join("");
  }

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
        || keywords.some(keyword => normalized.includes(keyword))) {
      return;
    }

    const current = ++generation;
    selectionRect = range.getBoundingClientRect();
    try {
      const hash = await keywordHash(normalized);
      if (current !== generation) return;
      const url = new URL(`https://github.com/${menu.dataset.repository}/new/${encodeURIComponent(menu.dataset.branch)}`);
      url.searchParams.set("filename", `blacklist/${hash}.txt`);
      url.searchParams.set("value", `${text}\n`);
      url.searchParams.set("message", `filter: add keyword ${text}`);
      url.searchParams.set("description", `建议将“${text}”加入标题和正文黑名单。\n来源页面：${location.origin}${location.pathname}\n\n请创建新分支并提交 PR；合并后由下一轮采集发布生效。`);
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
