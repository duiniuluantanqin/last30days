// Runs with github-script's authenticated client; never merges a PR.
module.exports = async function createKeywordPR({github, context, core}) {
  const {issue, action, repository} = context.payload;
  if (action !== "opened" || !issue || !issue.title.startsWith("[Block] ")) return;

  const marker = "<!-- forum-archive-block:v1 -->\n";
  const body = (issue.body || "").replace(/\r\n/g, "\n");
  if (!body.startsWith(marker)) throw new Error("缺少 Block 关键词标记");
  const payload = JSON.parse(body.slice(marker.length));
  const keyword = payload?.keyword;
  if (!payload || Object.keys(payload).length !== 1 || typeof keyword !== "string"
      || !keyword.trim() || keyword !== keyword.trim() || Array.from(keyword).length > 80
      || /[\p{C}\p{Zl}\p{Zp}]/u.test(keyword)) {
    throw new Error("关键词必须为 1–80 个字符的单行文字，不能包含控制字符");
  }
  const normalize = value => value.normalize("NFKC").toLowerCase().replace(/[\s\p{Cf}]/gu, "");
  const normalized = normalize(keyword);
  if (!normalized) throw new Error("关键词不能为空");

  const repo = context.repo;
  const branch = `block/issue-${issue.number}`;
  const existing = await github.rest.pulls.list({
    ...repo, head: `${repo.owner}:${branch}`, state: "all",
  });
  if (existing.data.length) {
    core.info(`已存在 PR：${existing.data[0].html_url}`);
    return;
  }

  let ref;
  try {
    ref = await github.rest.git.getRef({...repo, ref: `heads/${branch}`});
  } catch (error) {
    if (error.status !== 404) throw error;
  }
  const base = repository.default_branch;
  const source = ref || await github.rest.git.getRef({...repo, ref: `heads/${base}`});
  const path = "blacklist/keywords.txt";
  const {data: file} = await github.rest.repos.getContent({
    ...repo, path, ref: source.data.object.sha,
  });
  if (file.type !== "file" || file.encoding !== "base64") {
    throw new Error("无法读取黑名单文件");
  }
  const content = Buffer.from(file.content, "base64").toString("utf8");
  const covered = content.split(/\r?\n/).map(normalize).filter(Boolean)
    .some(word => normalized.includes(word));
  if (covered && !ref) {
    core.info("关键词已被主分支黑名单覆盖，无需创建 PR。");
    return;
  }
  if (!ref) {
    await github.rest.git.createRef({...repo, ref: `refs/heads/${branch}`, sha: source.data.object.sha});
  }
  if (!covered) {
    const updated = content + (content && !content.endsWith("\n") ? "\n" : "") + keyword + "\n";
    await github.rest.repos.createOrUpdateFileContents({
      ...repo, path, branch, sha: file.sha,
      message: `Add blacklist keyword from issue #${issue.number}`,
      content: Buffer.from(updated, "utf8").toString("base64"),
    });
  }
  const {data: pull} = await github.rest.pulls.create({
    ...repo, base, head: branch, title: `[Block] ${keyword}`,
    body: `由 Block Issue 自动追加一个关键词到 blacklist/keywords.txt。请仓库作者审核后手动合并；本任务不会合并 PR。\n\nCloses #${issue.number}`,
  });
  core.info(`已创建待作者审核的 PR：${pull.html_url}`);
};
