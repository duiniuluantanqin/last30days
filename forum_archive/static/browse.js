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

  for (const form of document.querySelectorAll(".page-jump")) {
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const page = Number(form.elements.page.value);
      if (!Number.isInteger(page)) return;
      const pagination = form.closest(".pagination");
      if (pagination.dataset.searchQuery !== undefined) {
        const url = new URL(`${basePath}/search.html`, location.origin);
        url.searchParams.set("q", pagination.dataset.searchQuery);
        url.searchParams.set("page", page);
        location.assign(url);
      } else {
        location.assign(page === 1 ? pagination.dataset.firstPage : pagination.dataset.pagePattern.replace("__PAGE__", page));
      }
    });
  }
})();
