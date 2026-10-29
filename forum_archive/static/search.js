(() => {
  "use strict";

  const query = new URLSearchParams(location.search);
  const text = (query.get("q") || "").trim();
  document.querySelector('.site-search input[name="q"]').value = text;
  const status = document.getElementById("search-status");
  const data = window.archiveSearchIndex;
  if (!data) {
    status.textContent = "搜索数据加载失败，请刷新页面后重试。";
    return;
  }
  const normalize = value => value.normalize("NFKC").toLowerCase().replace(/\s+/gu, "");
  const keyword = normalize(text);
  if (!keyword) {
    status.textContent = "请输入关键词，搜索全部已收录文章的标题和正文。";
    return;
  }
  const matches = data.articles.filter(article => normalize(article.title).includes(keyword) || normalize(article.text).includes(keyword));
  const pages = Math.max(1, Math.ceil(matches.length / data.pageSize));
  const requested = Number(query.get("page") || 1);
  const page = Number.isSafeInteger(requested) ? Math.max(1, Math.min(pages, requested)) : 1;
  status.textContent = `“${text}” 找到 ${matches.length} 篇讨论`;
  document.getElementById("search-page-count").textContent = matches.length ? `第 ${page} / ${pages} 页` : "";
  document.getElementById("search-empty").hidden = matches.length !== 0;
  const results = document.getElementById("search-results");
  results.hidden = matches.length === 0;
  const template = document.getElementById("search-result-template");
  for (const article of matches.slice((page - 1) * data.pageSize, page * data.pageSize)) {
    const row = template.content.cloneNode(true);
    const date = row.querySelector("time");
    date.dateTime = article.published_at;
    date.textContent = `${article.published_at.slice(5, 10).replace("-", ".")} ${article.published_at.slice(11, 16)}`;
    const title = row.querySelector("h2 a");
    title.textContent = article.title;
    if (!article.is_new) row.querySelector(".new-badge").remove();
    title.href = `/posts/${encodeURIComponent(article.id)}.html`;
    row.querySelector(".excerpt").textContent = article.summary || "打开阅读原帖内容。";
    const images = row.querySelector(".result-images");
    if (article.image_count) images.textContent = `${article.image_count} 张外链图片`;
    else images.remove();
    results.append(row);
  }
  if (!matches.length) return;

  const pagination = document.querySelector(".pagination");
  pagination.hidden = false;
  pagination.dataset.searchQuery = text;
  pagination.querySelector(".page-position").textContent = `第 ${page} / ${pages} 页`;
  const input = pagination.querySelector('input[name="page"]');
  input.max = pages;
  input.value = page;
  const links = pagination.querySelector(".pagination-links");
  links.replaceChildren();
  function add(label, number, current = false) {
    const node = document.createElement(number && !current ? "a" : "span");
    node.textContent = label;
    if (number) {
      node.className = "page-number";
      if (current) node.setAttribute("aria-current", "page");
      else {
        const url = new URL("/search.html", location.origin);
        url.searchParams.set("q", text);
        url.searchParams.set("page", number);
        node.href = url.pathname + url.search;
      }
    }
    links.append(node);
  }
  add("← 上一页", page > 1 ? page - 1 : null);
  const numbers = new Set([1, pages]);
  for (let number = Math.max(1, page - 2); number <= Math.min(pages, page + 2); number++) numbers.add(number);
  let previous = 0;
  for (const number of [...numbers].sort((a, b) => a - b)) {
    if (number - previous > 1) add("…", null);
    add(String(number), number, number === page);
    previous = number;
  }
  add("下一页 →", page < pages ? page + 1 : null);
})();
