(() => {
  const script = document.currentScript;
  if (location.origin !== script.dataset.origin) return;
  const endpoint = script.dataset.endpoint;
  const counter = document.querySelector("[data-analytics-counts]");
  let visitor = null;
  try {
    const key = "forum-archive-visitor";
    visitor = localStorage.getItem(key);
    if (!/^[a-f0-9]{32}$/.test(visitor || "")) {
      const bytes = new Uint8Array(16);
      if (globalThis.crypto?.getRandomValues) {
        crypto.getRandomValues(bytes);
      } else {
        for (let index = 0; index < bytes.length; index++) bytes[index] = Math.floor(Math.random() * 256);
      }
      visitor = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
      localStorage.setItem(key, visitor);
    }
  } catch {
    // Storage-disabled browsers contribute PV without a guessed unique visitor.
    visitor = null;
  }

  async function recordVisit() {
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({visitor}),
        credentials: "omit",
        cache: "no-store",
        keepalive: true,
      });
      if (!response.ok) return;
      const {today, total} = await response.json();
      if (!counter || ![today.uv, today.pv, total.uv, total.pv].every(value => Number.isSafeInteger(value) && value >= 0)) return;
      counter.textContent = `今日 UV ${today.uv} · PV ${today.pv} ｜ 累计 UV ${total.uv} · PV ${total.pv}`;
      counter.hidden = false;
    } catch {
      // Reading and navigation remain available while the counter is offline.
    }
  }

  recordVisit();
  window.addEventListener("pageshow", event => {
    if (event.persisted) recordVisit();
  });
})();
