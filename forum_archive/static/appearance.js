(() => {
  "use strict";
  const root = document.documentElement;
  const storageKey = "forum-archive-appearance";
  const skins = ["lilac", "sage", "sky", "sand"];
  let mode = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  let skin = "lilac";
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    if (saved?.mode === "light" || saved?.mode === "dark") mode = saved.mode;
    if (skins.includes(saved?.skin)) skin = saved.skin;
  } catch {}
  // Apply before the stylesheet paints, including when navigating to another page.
  root.dataset.mode = mode;
  root.dataset.skin = skin;

  function setup() {
    const controls = document.querySelector(".appearance-controls");
    const modeButtons = controls.querySelectorAll("[data-mode-choice]");
    const skinButtons = controls.querySelectorAll("[data-skin-choice]");
    const skinOptions = controls.querySelector(".skin-options");
    function apply() {
      root.dataset.mode = mode;
      root.dataset.skin = skin;
      modeButtons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.modeChoice === mode)));
      skinButtons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.skinChoice === skin)));
      skinOptions.hidden = mode === "dark";
    }
    function save() {
      apply();
      try {
        localStorage.setItem(storageKey, JSON.stringify({mode, skin}));
      } catch {}
    }
    modeButtons.forEach(button => button.addEventListener("click", () => {
      mode = button.dataset.modeChoice;
      save();
    }));
    skinButtons.forEach(button => button.addEventListener("click", () => {
      skin = button.dataset.skinChoice;
      save();
    }));
    apply();
    controls.hidden = false;

    const countdown = document.querySelector("[data-update-countdown]");
    const clock = countdown.querySelector("time");
    const beijingTime = new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
    function updateCountdown() {
      const now = Date.now();
      // Beijing has a whole-hour offset: each :32 aligns with minute 32 in UTC.
      let next = Math.floor(now / 3600000) * 3600000 + 32 * 60000;
      if (next <= now) next += 3600000;
      const seconds = Math.ceil((next - now) / 1000);
      clock.textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
      clock.dateTime = new Date(next).toISOString();
      countdown.title = `下次计划更新：北京时间 ${beijingTime.format(next)}（发布可能延迟）`;
      countdown.hidden = false;
    }
    updateCountdown();
    setInterval(updateCountdown, 1000);
    document.addEventListener("visibilitychange", updateCountdown);
    window.addEventListener("pageshow", updateCountdown);
  }
  document.addEventListener("DOMContentLoaded", setup, {once: true});
})();
