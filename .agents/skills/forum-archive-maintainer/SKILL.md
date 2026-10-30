---
name: forum-archive-maintainer
description: 维护本仓库的论坛采集、标题与正文黑名单、图片展示规则、Block 菜单及 GitHub Actions 到 ECS 的静态归档发布。用于相关规则变更、故障排查和回归验证。
---

# 论坛归档维护

先阅读仓库根目录的 `AGENTS.md`，再按任务读取附件；根目录 `README.md` 仅保留网站介绍和安装、构建方法。不要将本地验证结果表述为已在 ECS 生效。

- 内容过滤、图片规则、搜索、New 标记、采集预算和 Block 行为：读取 [内容与展示规则](references/rules.md)。
- 本地采集、验证、数据目录、GitHub Actions、ECS 发布、备份和版本清理：读取 [运行、测试与部署](references/operations.md)。其中服务器配置步骤仅供参考，Caddy 由用户自行维护。

## 选择修改位置

- 页面结构或原图链接：检查 `forum_archive/source.py` 的 `parse_post`、`clean_content`，以及 `tests/test_archive.py` 的主帖/回复和图片请求测试。采集器不能请求图片。
- 今日采集：列表入口使用 `search=today`，分页保留该参数，只抓北京时间当天的待采主帖，详情页再次核对楼主发布时间；不恢复历史补采。分页游标跨日重置，旧补采 meta 不参与运行。优先读取楼主 `.tipad` 结构化时间，兼容 `Posted: MM-DD HH:MM 樓主`，不能使用回复时间；保留跨年测试。迁移同版块普通列表 URL 时保留既有 SQLite 归档，仍按发布时间倒序与 30 天规则展示。
- 黑名单：检查 `config.py` 的 `TitleFilter`、`crawler.py` 的采集复查和 `store.py` 的 `apply_filter`。新帖与已有帖必须使用同一套标题和全文规则，正文不能只用 180 字摘要代替。保留规范化、跨标签与 HTML 实体处理。
- 图片数量：清理后的原始正文超过 20 个 `img` 标签时过滤整篇，20 张保留，重复 URL 仍按标签计数。规则在 `TitleFilter.blocks` 中统一用于采集与重筛，不使用展示层隐藏后的图片数或可能陈旧的数据库计数；测试覆盖 20/21 边界及历史页面清理。
- 词库仅依赖本地 `blacklist/keywords.txt`；原上游的色情和广告规则已迁入，来源和许可证见 `blacklist/SOURCES.md`，不要恢复子模块更新步骤。少数迁入规则刻意保留逗号以维持原匹配范围，不能无差别剥除所有词的标点。
- 图片隐藏：检查 `site.py` 的构建处理和 `templates/post.html`、`index.html`。在文章通过数量过滤后仅改变公开展示，保持数据库原始归档可重建；同步更新剩余图片数量、图片提示和必要的摘要文字。具体条件见 `references/rules.md` 与当前测试。
- 搜索和分页：检查 `site.py`、`templates/search.html`、`templates/pagination.html`、`static/browse.js` 和 `static/search.js`。搜索数据只在搜索页加载，包含公开文章的完整可见正文纯文本；不得包含已屏蔽或过期内容。下架时本地构建与远程 `purge-blocked.sh` 均使旧全文索引失效，保护当前索引。浏览器验证使用 `tests/browser_site_search.py` 的临时数据，检查全文、月份路径、搜索词保留、跳转边界及 CSP。
- 新文章标记：`crawler.py` 每次完成爬取将成功新增 ID 写入 SQLite meta 的 `new_article_ids`，零新增写空清单；`site.py` 为公开文章生成 `is_new`，同步列表、文章页及搜索数据。不可根据发布时间或最后一次成功更新时间推断，不得在单独构建时轮换批次；回归测试覆盖部分失败、零新增及列表错误。
- Block：检查 `static/keyword-menu.js` 和 `templates/base.html`。单按钮复制划词并打开 `blacklist/keywords.txt` 的 GitHub 编辑页，由用户在末尾粘贴保存；目标仓库和分支由 `site.github_repository`、`site.github_branch` 配置。不创建 Issue 或 PR，不引入 OAuth 服务或浏览器令牌。普通 HTTP 须验证复制及失败提示，不得恢复逐词生成哈希文件的流程。
- 历史文章清理或发布：检查 `site.py`、`scripts/remote.py`、`scripts/purge-blocked.sh` 和 `archive.yml`。黑名单变更须先应用到恢复的服务器数据库，再生成新站点和删除清单；旧 HTML 通过数字帖子 ID 精确删除。源站访问失败不能阻止已完成的下架。不要把公开输出目录或 Actions 缓存当权威数据库。
- 流水线采集由 `schedule` 和 `workflow_dispatch` 事件执行；两者均先恢复 ECS 状态、重筛、构建和清理发布，再采集并发布新内容。push 事件只清理发布，合并关键词 PR 不触发源站采集。
- GitHub Pages 使用独立的 `pages.yml`，在 Hourly archive 结束后或手动触发时只读恢复 ECS 数据库、重筛和发布，不重复采集或写回 ECS。通过 `build --base-path` 适配项目站点前缀；默认构建仍使用根路径。只上传当次公开版本目录，设置与验证见运维说明。
- 磁盘保留与备份：`remote.py` 在切换前滚动备份旧状态到 `state/archive.sqlite3.bak`，失败不得继续切站。发布和文章下架成功后才运行 `scripts/prune-deployments.sh`，保留最新 3 个数字编号版本并额外保护 `current`，清理本次上传及超过 24 小时的已知上传残留；不删除状态目录，不遍历软链接或无关文件。相关真实 shell 与模拟发布测试在 `tests/test_deployment_retention.py`。

## 验证

先用最小离线用例复现目标行为，再修改代码。规则变化至少覆盖命中、不命中、阈值边界和已有归档重建；确认不会修改数据库原文、误删未命中文章或遗留错误的图片数量。

图片规则依次为：原始正文超过 20 张图则过滤整篇；通过后，“防水图”移字并隐藏全帖图片；正文至少 200 个非空白字符且图片仅位于文末时隐藏全部图片。检验文末规则时区分末尾空白/容器与真正的图片后文字，保留正文中间配图的文章。

从仓库根目录运行：

```powershell
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -v
.venv/Scripts/python.exe -m forum_archive demo
git diff --check
```

Linux 使用当前虚拟环境的 `python`。默认运行不需要拉取第三方词库；若用户配置了额外的 `filter.keyword_files`，缺失文件须报告其路径，不能静默跳过。

涉及菜单交互时，先启动独立演示预览，再使用安装了 Playwright 的 Python 运行：

```powershell
.venv/Scripts/python.exe -m forum_archive serve --demo --port 8768
python tests/browser_keyword_menu.py --url http://127.0.0.1:8768/ --http-origin
```

复用已存在的预览进程或选择空闲端口；不要任意终止其他进程。浏览器测试覆盖剪贴板并拦截 GitHub 导航，不得实际编辑远端文件。服务器 Caddy 配置由用户自行维护。部署与线上清理仅在本次授权范围内执行，未部署时给出推送后由现有流水线生效的说明。
