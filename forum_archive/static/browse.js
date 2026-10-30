(() => {
  "use strict";
  const basePath = document.documentElement.dataset.basePath || "";

  const search = document.querySelector(".site-search");
  search.addEventListener("submit", event => {
    // Navigate with JS so existing CSP form-action 'none' remains compatible.
    event.preventDefault();
    const url = new URL(`${basePath}/search.html`, location.origin);
    const query = search.elements.q.value.trim();
    if (query) url.searchParams.set("q", query);
    location.assign(url);
  });

  const backToTop = document.querySelector(".back-to-top");
  const updateBackToTop = () => { backToTop.hidden = window.scrollY <= 0; };
  window.addEventListener("scroll", updateBackToTop, { passive: true });
  updateBackToTop();
  backToTop.addEventListener("click", () => window.scrollTo({ top: 0 }));

  window.archiveInfiniteScroll = (hasNext, loadNext) => {
    if (!("IntersectionObserver" in window)) return;
    const loader = document.querySelector(".feed-loader");
    const status = loader.querySelector(".feed-status");
    const retry = loader.querySelector(".feed-retry");
    let more = hasNext;
    let loading = false;
    loader.hidden = false;
    status.textContent = more ? "继续下滑加载更多文章" : "到底啦，这是我的底线了。";
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) load();
    });
    async function load() {
      if (loading || !more) return;
      loading = true;
      observer.unobserve(loader);
      retry.hidden = true;
      status.textContent = "正在加载更多文章…";
      try {
        more = await loadNext();
        status.textContent = more ? "继续下滑加载更多文章" : "到底啦，这是我的底线了。";
        if (more) observer.observe(loader);
      } catch {
        status.textContent = "加载失败，请点击重试加载。";
        retry.hidden = false;
      } finally {
        loading = false;
      }
    }
    retry.addEventListener("click", load);
    if (more) observer.observe(loader);
  };

  const list = document.querySelector(".article-list:not(#search-results)");
  if (!list) return;
  const loader = document.querySelector(".feed-loader");
  let next = loader.dataset.nextPage;
  window.archiveInfiniteScroll(Boolean(next), async () => {
    const response = await fetch(next);
    if (!response.ok) throw new Error("Page load failed");
    const nextDocument = new DOMParser().parseFromString(await response.text(), "text/html");
    const rows = nextDocument.querySelectorAll(".article-list > .article-row");
    const navigation = nextDocument.querySelector(".feed-loader");
    if (!rows.length || !navigation) throw new Error("Missing article list");
    next = navigation.dataset.nextPage;
    list.append(...rows);
    loader.dataset.nextPage = next;
    return Boolean(next);
  });
})();
