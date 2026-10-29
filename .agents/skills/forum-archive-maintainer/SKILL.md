---
name: forum-archive-maintainer
description: 维护本仓库的论坛采集、标题与正文黑名单、图片展示规则、Block 菜单及 GitHub Actions 到 ECS 的静态归档发布。用于相关规则变更、故障排查和回归验证。
---

# 论坛归档维护

先阅读仓库根目录的 `AGENTS.md`，按当前任务查阅 `README.md` 的规则、测试或部署章节。不要将本地验证结果表述为已在 ECS 生效。

## 选择修改位置

- 页面结构或原图链接：检查 `forum_archive/source.py` 的 `parse_post`、`clean_content`，以及 `tests/test_archive.py` 的主帖/回复和图片请求测试。采集器不能请求图片。
- 黑名单：检查 `config.py` 的 `TitleFilter`、`crawler.py` 的采集复查和 `store.py` 的 `apply_filter`。新帖与已有帖必须使用同一套标题和全文规则，正文不能只用 180 字摘要代替。保留规范化、跨标签与 HTML 实体处理。
- 词库仅依赖本地 `blacklist/keywords.txt`；原上游的色情和广告规则已迁入，来源和许可证见 `blacklist/SOURCES.md`，不要恢复子模块更新步骤。少数迁入规则刻意保留逗号以维持原匹配范围，不能无差别剥除所有词的标点。
- 图片隐藏：检查 `site.py` 的构建处理和 `templates/post.html`、`index.html`。仅改变公开展示，保持数据库原始归档可重建；同步更新剩余图片数量、图片提示和必要的摘要文字。具体条件以 README 与当前测试为准。
- 搜索和分页：检查 `site.py`、`templates/search.html`、`templates/pagination.html`、`static/browse.js` 和 `static/search.js`。搜索数据只在搜索页加载，包含公开文章的完整可见正文纯文本；不得包含已屏蔽或过期内容。下架时本地构建与远程 `purge-blocked.sh` 均使旧全文索引失效，保护当前索引。浏览器验证使用 `tests/browser_site_search.py` 的临时数据，检查全文、月份路径、搜索词保留、跳转边界及 CSP。
- Block：检查 `static/keyword-menu.js`、`templates/base.html`、`scripts/block-keyword-pr.cjs` 和 `block-keyword.yml`。单按钮打开预填 Issue，提交后由 Actions 在独立分支追加到 `blacklist/keywords.txt` 并创建面向仓库实际默认分支的 PR，作者手动合并才生效。不得将 Issue 输入作为脚本执行、在浏览器存令牌、直接写主分支或自动合并。普通 HTTP 也须验证；不再依赖复制或手动编辑文件。
- 历史文章清理或发布：检查 `site.py`、`scripts/remote.py`、`scripts/purge-blocked.sh` 和 `archive.yml`。黑名单变更须先应用到恢复的服务器数据库，再生成新站点和删除清单；旧 HTML 通过数字帖子 ID 精确删除。源站访问失败不能阻止已完成的下架。不要把公开输出目录或 Actions 缓存当权威数据库。
- 流水线采集仅由 `schedule` 事件执行；push 和 `workflow_dispatch` 事件只恢复 ECS 状态、重筛、构建和清理发布。不要让手动任务或合并关键词 PR 触发源站采集。
- 磁盘保留与备份：`remote.py` 在切换前滚动备份旧状态到 `state/archive.sqlite3.bak`，失败不得继续切站。发布和文章下架成功后才运行 `scripts/prune-deployments.sh`，保留最新 3 个数字编号版本并额外保护 `current`，清理本次上传及超过 24 小时的已知上传残留；不删除状态目录，不遍历软链接或无关文件。相关真实 shell 与模拟发布测试在 `tests/test_deployment_retention.py`。

## 验证

先用最小离线用例复现目标行为，再修改代码。规则变化至少覆盖命中、不命中、阈值边界和已有归档重建；确认不会修改数据库原文、误删未命中文章或遗留错误的图片数量。

图片规则目前包括：“防水图”移字并隐藏全帖图片；正文至少 200 个非空白字符且图片仅位于文末时隐藏全部图片、不限数量。检验后一规则时区分末尾空白/容器与真正的图片后文字，保留正文中间配图的文章。

从仓库根目录运行：

```powershell
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -v
.venv/Scripts/python.exe -m forum_archive demo
git diff --check
node --test tests/test_block_keyword_pr.cjs
```

Linux 使用当前虚拟环境的 `python`。默认运行不需要拉取第三方词库；若用户配置了额外的 `filter.keyword_files`，缺失文件须报告其路径，不能静默跳过。

涉及菜单交互时，先启动独立演示预览，再使用安装了 Playwright 的 Python 运行：

```powershell
.venv/Scripts/python.exe -m forum_archive serve --demo --port 8768
python tests/browser_keyword_menu.py --url http://127.0.0.1:8768/ --http-origin
```

复用已存在的预览进程或选择空闲端口；不要任意终止其他进程。浏览器测试拦截 GitHub 导航，不得真正提交 Issue 或 PR。Issue 转 PR 的离线测试使用模拟 API；配置和令牌权限限制见根目录 README。部署与线上清理仅在本次授权范围内执行，未部署时给出推送后由现有流水线生效的说明。
