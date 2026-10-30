# 运行、测试与部署

所有命令从仓库根目录执行。服务器及生产 Caddy 配置由用户自行维护，以下部署步骤仅供参考；本地验证不代表已部署。

目录：

- [本地运行（Windows）](#本地运行windows)
- [Linux](#linux)
- [文件与数据](#文件与数据)
- [测试](#测试)
- [GitHub Actions 与 ECS 数据库](#github-actions-与-ecs-数据库本地测试不会执行部署)
- [发布到 GitHub Pages](#发布到-github-pages)

## 本地运行（Windows）

需要 Python 3.11+。本项目已用 Python 3.14 本地验证，Actions 使用 Python 3.12。

如果安装了 uv：

```powershell
uv --cache-dir .cache/uv venv --python python .venv
uv --cache-dir .cache/uv pip install --python .venv/Scripts/python.exe -e .
```

或在全新环境中使用标准 venv：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
```

先用小批量验证真实来源：

```powershell
.venv/Scripts/python.exe -m forum_archive crawl --max-posts 3 --recent-pages 1
.venv/Scripts/python.exe -m forum_archive serve --reload --port 8766
```

打开 <http://127.0.0.1:8766>。预览服务只监听本机。`--reload` 会在启动时重建已有数据，保存 Python、模板、CSS/JS、配置或词库后自动重建并重启子进程，浏览器随后自动刷新；另开终端采集并生成新版本后也会自动刷新。预览响应禁止缓存，开发刷新脚本仅由预览服务插入，不写入发布文件。监听不扫描生成目录或 Python 缓存，避免反复触发构建。

一个端口只能运行一个预览服务，包括 Windows；端口占用时启动命令报错，不再让新旧实例同时响应。将已有预览切换到 `--reload` 时，先在旧终端按 Ctrl+C 停止，再运行新命令；之后修改代码无需手动重启。不加 `--reload` 时仍只提供现有静态版本，不监视源码变化。

继续按配置采集今日主帖、查看进度：

```powershell
.venv/Scripts/python.exe -m forum_archive crawl
.venv/Scripts/python.exe -m forum_archive status
```

独立演示模式不会访问来源，也不会混入真实数据库：

```powershell
.venv/Scripts/python.exe -m forum_archive demo
.venv/Scripts/python.exe -m forum_archive serve --demo --reload --port 8765
```

## Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m forum_archive crawl --max-posts 3 --recent-pages 1
.venv/bin/python -m forum_archive serve --reload --port 8766
```

## 文件与数据

```text
config.toml                      来源、黑名单与运行预算
blacklist/blacklist.txt            合并后的统一黑名单关键词
blacklist/SOURCES.md             迁入词库的来源、版本及修改说明
blacklist/licenses/              迁入词库的许可证与版权声明
forum_archive/                   采集、状态、渲染与本地预览
forum_archive/templates/         阅读页模板
forum_archive/static/            CSS 与 JavaScript，无图片资源下载
data/archive.sqlite3             真实采集数据和断点（不提交 Git）
data/demo/archive.sqlite3        独立演示数据（不提交 Git）
site/release-*/                  生成的公开网页（不提交 Git）
site/current.json                本地当前版本指针
scripts/remote.py                ECS 数据库恢复、备份与写回
deploy/Caddyfile.example         旧 ECS 网站的 Caddy 配置参考
AGENTS.md                       项目维护约束、代码入口和验证命令
.agents/skills/forum-archive-maintainer/SKILL.md  项目专用维护技能
tests/                          离线回归测试，不访问源站
```

SQLite 包含结构化正文与状态，是权威归档，需要备份。公开版本只包含网页、CSS 与 JavaScript，不包含数据库、日志、密钥和下载的图片。每次构建到新目录，完成后原子切换版本指针，失败不会覆盖旧站点。

## 测试

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

覆盖标题和全文规则、20/21 张图片边界与历史页面下架、正文命中后的持续屏蔽、真实页面结构的时间戳、来源版块限制、主帖与回复分离、正文清理、懒加载原图链接、零图片请求、去重、失败重试、今日分页与跨日重置、楼主 Posted 时间及跨年解析、同版块状态迁移、robots 限速和重新筛选后的旧页面下线。

可选浏览器检查需要在执行该命令的 Python 环境中安装 Playwright 和可用的 Chromium/Chrome。先启动演示预览，再运行 `.venv/Scripts/python.exe -X utf8 tests/browser_keyword_menu.py --url http://127.0.0.1:8765/`。检查实际鼠标划词、GitHub 新建文件链接、预填内容、独立文件名、重复词、长度限制和手机弹窗位置，不向 GitHub 提交任何内容。测试不使用系统剪贴板。

`.venv/Scripts/python.exe -X utf8 tests/browser_site_search.py` 使用临时数据库和随机本地端口，自动验证标题/全文搜索、连续滚动加载、月份范围、加载失败重试、页码控件移除、文章在新选项卡打开且保留原列表、短距离滚动后的回顶部、HTML 转义、手机布局及现有 CSP。该命令需在同时装有本项目依赖和 Playwright 的 Python 环境中运行，不访问源站或提交 GitHub 内容。

`.venv/Scripts/python.exe -X utf8 tests/browser_preview.py` 在临时目录复制应用，验证配置、CSS、JavaScript、模板、Python 响应头和词库修改后浏览器自动刷新，不改动工作区源码或真实数据库。

追加 `--http-origin` 可将本地页面映射到普通 HTTP 测试来源，验证 Chrome 中 `isSecureContext=false` 时的 `Block` 菜单、新建文件链接及预填功能；同时校验中文、emoji 和特殊字符。

## GitHub Actions 与 ECS 数据库（本地测试不会执行部署）

网页仅发布到 GitHub Pages；ECS 只保存 SQLite 权威数据库，不再接收网页或切换网站入口。数据库仍然必需，保存原始正文、30 天归档、去重 ID、采集状态、今日分页游标、失败重试及 NEW 批次；Pages 文件和 Actions 缓存不能代替数据库。

`test.yml` 在 push/PR 执行离线测试。`archive.yml` 在 `master` 分支每次 push 后只清理并保存已有数据（包括合并 PR）；每小时定时任务和手动触发均执行源站采集。手动运行入口为 **Actions → Hourly archive → Run workflow**，选择 `master` 后确认。定时任务只使用默认分支，可能延迟，不承诺严格准点。更换默认分支时同步修改工作流的 `push.branches` 和 Pages 分支限制。一次 push 包含多个 commit 时执行一次。

继续使用现有 Ubuntu ECS 上的独立数据目录和**非 root 部署用户**，使其可读写 `/var/www/html/last30days/state` 和 `incoming`。GitHub Pages 发布无需将网站域名解析到 ECS，也无需新增 Caddy 站点。

在 GitHub 仓库配置这些 Secrets：

| Secret | 内容 |
|---|---|
| `DEPLOY_HOST` | ECS 域名或公网 IPv4 |
| `DEPLOY_USER` | 独立的非 root 部署用户 |
| `DEPLOY_SSH_KEY` | 专用 SSH 私钥，仅供该用户登录 |
| `DEPLOY_KNOWN_HOSTS` | 通过可信方式核验的 ECS SSH 主机公钥记录；自定义端口应使用对应记录格式 |

可选 Repository Variables：`DEPLOY_ROOT`（默认 `/var/www/html/last30days`）、`DEPLOY_PORT`（默认 `22`）。服务器端仍使用以下路径：

```text
/var/www/html/last30days/
  state/archive.sqlite3             权威数据与进度，不公开
  state/archive.sqlite3.bak         替换前的上一份数据库，不公开
  incoming/state-<编号>.sqlite3     数据库上传暂存文件，保存后移入 state
```

所有采集任务先执行：恢复 ECS 状态 → 应用黑名单与 30 天保留规则、构建 → `remote.py save-state` 写回数据库。push 到此结束；定时和手动任务继续限速采集 → 重新构建 → 再次保存数据库。任务结束后由 Pages 工作流读取数据库并发布网页。采集任务互斥，SSH 使用严格的主机指纹校验。

每次保存会先将服务器原有数据库复制到暂存文件，再原子替换 `state/archive.sqlite3.bak`，随后原子替换当前数据库。备份只保留上一份，首次保存没有旧数据库时不创建备份；备份失败会停止，保留当前数据库。该备份与数据库位于同一磁盘，用于回退，不能替代异地备份。

成功保存后，仅清理 `incoming/` 中超过 **24 小时**的 `state-<编号>*.sqlite3` 数据库暂存文件，包括失败时留下的备份暂存文件；近期上传、无关文件、子目录及软链接不遍历，不清理 `state/`。

原 ECS 网站的 `current`、`releases/` 和 Caddy 配置不再由流水线更新或清理，旧网址若继续开放会保留旧内容，包括后续被屏蔽或过期的文章。`remote.py publish`、`purge-blocked.sh` 和 `prune-deployments.sh` 仅保留为旧网站的手动工具；停用旧网址由用户另行处理。

个别主帖失败时仍会保存成功内容和重试状态，再标记工作流失败；Pages 会继续发布已保存的数据。列表解析失败时不保存该轮新增内容，Pages 仍可发布采集前已保存的清理结果。SSH 写回失败会报告错误；Pages 读取最后保存的快照。CLI 退出码：`0` 成功，`1` 关键错误，`2` 部分主帖失败。

**费用与磁盘：** 源站的 10 秒请求间隔使默认 60 篇上限的一轮任务可能运行十余分钟。每小时运行请先测量实际时长，再按 GitHub 当前套餐评估，不能假设私有仓库免费额度足够。SQLite 会清除过期正文并保留去重元数据；数据库文件不会因此立即缩小，元数据仍会随采集增加。数据库、备份及暂存文件可能含有原始或被屏蔽内容，不能将它们公开作为网站根目录。

本项目不包含登录、验证码绕过或访问限制规避。若 GitHub 运行环境无法正常访问来源，工作流会报告失败，需要重新评估执行环境。

## 发布到 GitHub Pages

`.github/workflows/pages.yml` 是唯一的网页发布工作流。它在 `master` 分支的 **Hourly archive** 工作流结束后自动运行，也可通过 **Actions → Publish GitHub Pages → Run workflow** 选择 `master` 手动运行。自动触发需要该工作流已存在于默认分支。

Pages 工作流只读取 ECS 的 `state/archive.sqlite3` 快照，使用当前 `master` 的黑名单和 30 天保留规则重新构建，不采集源站、不向 ECS 写回数据库或网页。即使 Hourly archive 因采集失败而结束，也会尝试发布 ECS 已保存的归档，避免漏掉采集前完成的下架。若 ECS 无法连接或尚无数据库则失败，不用空数据库覆盖已有 Pages。数据库保存和 Pages 发布并非同一事务；Pages 失败可单独重试。

首次启用：

1. 在仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。私有仓库需确认当前 GitHub 套餐支持 Pages；Pages 的访问权限以仓库 Pages 设置为准。
2. 沿用现有 `DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS` Secrets，以及可选 `DEPLOY_ROOT`、`DEPLOY_PORT` Variables，无需额外的个人访问令牌。
3. 将代码推送到默认分支 `master`。首次应先让 Hourly archive 保存数据库，再运行 Publish GitHub Pages。若 `github-pages` environment 配置了审批或部署分支限制，需在仓库设置中允许 `master` 部署。
4. 发布成功后访问 <https://duiniuluantanqin.github.io/last30days/>，确认首页样式、月份归档、文章、全文搜索、滚动加载及回顶部按钮。

`actions/configure-pages` 提供站点的 `base_path`，构建时通过 `build --base-path` 传入。默认项目站点使用 `/last30days`；设置独立域名后根据 Pages 返回的路径构建。普通 `build` 不传此参数，继续使用根目录路径。只上传本次构建的 `release-*` 目录内容，不上传 `site/` 的历史版本、SQLite、凭据或日志；Pages artifact 保留 1 天。Pages 每次部署完整站点，当前版本不包含已屏蔽或过期文章。

本地验证路径适配（临时数据库，不访问源站）：

```powershell
.venv/Scripts/python.exe -X utf8 tests/browser_site_search.py
.venv/Scripts/python.exe -X utf8 tests/browser_site_search.py --base-path /last30days
```
