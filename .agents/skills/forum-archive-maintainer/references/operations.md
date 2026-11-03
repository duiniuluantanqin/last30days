# 运行、测试与部署

所有命令从仓库根目录执行。服务器基础环境及生产 Caddy 配置由用户自行维护，统计服务由独立工作流自动部署；本地验证不代表已部署。

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
scripts/deploy-analytics.sh      ECS 统计服务安装、切换与失败回退
deploy/Caddyfile.example         旧 ECS 网站的 Caddy 配置参考
deploy/analytics.service.example 统计用户服务模板，由流水线填充部署目录
AGENTS.md                       项目维护约束、代码入口和验证命令
.agents/skills/forum-archive-maintainer/SKILL.md  项目专用维护技能
tests/                          离线回归测试，不访问源站
```

SQLite 包含结构化正文与状态，是权威归档，需要备份。公开版本只包含网页、CSS 与 JavaScript，不包含数据库、日志、密钥和下载的图片。每次构建到新目录，完成后原子切换版本指针，失败不会覆盖旧站点。

启用自建访问统计时，ECS 额外保存 `state/analytics.sqlite3`，由常驻统计接口直接写入，独立于会被 Actions 替换的 `state/archive.sqlite3`。统计库不上传 Pages、不随文章过期清理，也不由 `remote.py restore/save-state` 同步；备份需单独安排，见下文。

## 测试

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

覆盖标题和全文规则、20/21 张图片边界与历史页面下架、正文命中后的持续屏蔽、真实页面结构的时间戳、来源版块限制、主帖与回复分离、正文清理、懒加载原图链接、零图片请求、去重、失败重试、今日分页与跨日重置、楼主 Posted 时间及跨年解析、同版块状态迁移、robots 限速和重新筛选后的旧页面下线。

可选浏览器检查需要在执行该命令的 Python 环境中安装 Playwright 和可用的 Chromium/Chrome。先启动演示预览，再运行 `.venv/Scripts/python.exe -X utf8 tests/browser_keyword_menu.py --url http://127.0.0.1:8765/`。检查实际鼠标划词、GitHub 新建文件链接、预填内容、独立文件名、重复词、长度限制和手机弹窗位置，不向 GitHub 提交任何内容。测试不使用系统剪贴板。

`.venv/Scripts/python.exe -X utf8 tests/browser_site_search.py` 使用临时数据库和随机本地端口，自动验证标题/全文搜索、连续滚动加载、月份范围、加载失败重试、页码控件移除、文章在新选项卡打开且保留原列表、短距离滚动后的回顶部、HTML 转义、手机布局及现有 CSP。该命令需在同时装有本项目依赖和 Playwright 的 Python 环境中运行，不访问源站或提交 GitHub 内容。

`.venv/Scripts/python.exe -X utf8 tests/browser_appearance.py` 使用临时归档验证右上角外观按钮、四种浅色皮肤、深色模式、跨页面/刷新保留选择、系统默认色调、禁用存储、键盘切换，以及北京时间每小时 `:32` 倒计时的小时和午夜边界，检查倒计时同行和桌面、平板、手机布局；追加 `--http-origin` 验证普通 HTTP。截图写入 `.cache/screenshots/appearance-*.png`。本地演示也可以查看外观和倒计时，UV/PV 仍不在演示模式上报。

`.venv/Scripts/python.exe -X utf8 tests/browser_preview.py` 在临时目录复制应用，验证配置、CSS、JavaScript、模板、Python 响应头和词库修改后浏览器自动刷新，不改动工作区源码或真实数据库。

追加 `--http-origin` 可将本地页面映射到普通 HTTP 测试来源，验证 Chrome 中 `isSecureContext=false` 时的 `Block` 菜单、新建文件链接及预填功能；同时校验中文、emoji 和特殊字符。

## GitHub Actions 与 ECS 数据库（本地测试不会执行部署）

网页仅发布到 GitHub Pages；归档流水线在 ECS 只保存 SQLite 权威数据库，不再上传网页或切换网站入口。可选的 UV/PV 接口在 ECS 单独常驻运行。文章数据库仍然必需，保存原始正文、30 天归档、去重 ID、采集状态、今日分页游标、失败重试及 NEW 批次；Pages 文件和 Actions 缓存不能代替数据库。

`test.yml` 在 push/PR 执行离线测试。`archive.yml` 在 `master` 分支每次 push 后只清理并保存已有数据（包括合并 PR）；每小时定时任务和手动触发均执行源站采集。手动运行入口为 **Actions → Hourly archive → Run workflow**，选择 `master` 后确认。定时任务只使用默认分支，可能延迟，不承诺严格准点。更换默认分支时同步修改工作流的 `push.branches` 和 Pages 分支限制。一次 push 包含多个 commit 时执行一次。

继续使用现有 Ubuntu ECS 上的独立数据目录和**非 root 部署用户**，使其可读写 `/var/www/html/last30days/state` 和 `incoming`。仅发布 GitHub Pages 无需将网站域名解析到 ECS；若启用下文的自建统计，额外为统计接口准备 ECS HTTPS 域名。

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

## 自建 UV/PV 统计（可选）

GitHub Pages 无法运行写库服务。项目自己的 `assets/analytics.js` 在页面打开、刷新或从浏览器后退缓存恢复时，向 ECS 的 `/api/analytics` 发送一个随机浏览器标识；接口写入 `state/analytics.sqlite3` 并返回计数，左侧固定侧栏显示今日和累计 UV/PV，手机端显示在页面上方。无需 Umami、第三方统计 SDK、外部统计平台或新增 Python 依赖。

PV 每打开一个完整页面加一，滚动预加载的 HTML、读取计数、修改搜索参数和接口预检不增加 PV。今日按北京时间划分；今日 UV 按当日不同浏览器标识去重，累计 UV 按全部历史不同标识去重，不能把每日 UV 相加。标识保存在站点的 localStorage 中，跨标签页复用；清理存储、更换浏览器或设备会成为新访客。浏览器禁用存储时仅计 PV。统计不会回填启用前的历史访问。

请求只包含 `visitor`，不上传页面地址、搜索词、标题、正文、来源页或浏览器信息；统计库只保存日期、随机标识及 PV，不保存 IP，应用也不记录 HTTP 访问日志。接口按完整 Origin 放行且不使用 Cookie/登录凭据；Origin 校验不能阻止自行伪造 HTTP 请求，所以这些数字适合作为访问量参考。

统计代码由 `.github/workflows/analytics.yml`（**Deploy analytics to ECS**）独立部署：相关 Python 代码、依赖、统计配置或部署脚本推送到 `master` 后自动运行，也可在 Actions 中选择该工作流和 `master` 手动运行。流程复用文章数据库部署的 `DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS` Secrets 和 `DEPLOY_ROOT`、`DEPLOY_PORT` Variables，不需要新增 Secret。网页仍只由 Pages 发布，采集和数据库写回流程不变。

首次启用：

1. ECS 需要可用的 `python3`（3.11+）、对应的 venv 模块、systemd 和访问 Python 包源的网络。沿用现有非 root 部署用户，确保其能写入 `DEPLOY_ROOT`、`state/` 和自己的 `~/.config/systemd/user/`。管理员只需为该用户启用一次 linger，让用户服务在 SSH 退出后和系统重启后继续运行：

   ```bash
   # 将 archive 换成 DEPLOY_USER 对应的实际用户名。
   sudo loginctl enable-linger archive
   ```

   流水线不会安装系统软件或执行 sudo；环境缺失会在切换旧服务前失败。若此前按旧模板安装过系统级 `forum-analytics.service`，需先执行 `sudo systemctl disable --now forum-analytics.service`，再运行流水线迁移到用户服务；统计库仍沿用原路径。
2. 设置 `config.toml`：

   ```toml
   [site]
   # 合并到已有 [site]，不要再建立第二个同名表。
   analytics_endpoint = "https://stats.last30days.cn/api/analytics"
   analytics_origin = "https://duiniuluantanqin.github.io"
   ```

   `analytics_origin` 只填 Pages 的来源（协议和域名，无末尾斜线或 `/last30days` 路径）；使用 Pages 自定义域名时填写实际来源。流水线从该配置生成 ECS 最小运行配置，不上传词库、文章数据或公开资源。仓库已填入上述统计地址；将 `analytics_endpoint` 设为空字符串即可关闭页面统计，此时部署工作流会拒绝部署且保留现有服务；配置的来源与当前页面不同也不会上报，本地演示始终关闭统计。
3. 将 `stats.last30days.cn` 解析到 ECS。参考 `deploy/analytics.Caddyfile.example`，在用户维护的 Caddy 配置中增加统计域名的独立 HTTPS 反向代理块，仅转发 `/api/analytics` 到 `127.0.0.1:3030`。不要把 `state/` 暴露给 `file_server`，也不要覆盖已有 Caddy 配置。浏览器中的 Pages 是 HTTPS，统计接口也必须使用有效 HTTPS 证书；3030 只监听本机。
4. 将代码推送到 `master`，或在 **Actions → Deploy analytics to ECS → Run workflow** 手动运行。无需手动上传 Python 或安装服务：工作流先运行统计和部署回退测试，再复用 SSH 连接上传 Python 包、最小配置和用户服务模板，创建独立 venv 并安装依赖，自动注册、启用和重启 `forum-analytics.service`。本机 GET/CORS 检查失败会恢复上个代码版本和服务模板；首次启动失败会停用新服务。公网 HTTPS 检查另行执行，若仅该步骤失败，应检查域名、证书和 Caddy，本机已成功部署的服务会保留。
5. 使用以下只读请求检查来源、CORS 与数据库连接；GET 不会增加计数：

   ```bash
   curl -i https://stats.last30days.cn/api/analytics \
     -H 'Origin: https://duiniuluantanqin.github.io'
   ```

   然后推送配置并通过现有 Pages 工作流发布，打开网站检查侧栏统计。首次访问应为 UV/PV 各 1，同一浏览器刷新只增加 PV。接口离线时隐藏计数，阅读、搜索和导航继续工作。若用户另设 CSP，需在 `connect-src` 中加入统计接口来源；本地预览会按配置自动加入。

默认 ECS 目录（使用自定义 `DEPLOY_ROOT` 时随之变化）：

```text
/var/www/html/last30days/
  analytics/releases/analytics-<编号>/  每次部署的代码、配置和独立 .venv
  analytics/active                     当前统计服务代码软链接，与旧网站 current 无关
  incoming/analytics-<编号>.tar.gz      统计代码上传包
  state/analytics.sqlite3              持续写入的统计库，不随代码更新替换
```

服务配置位于部署用户的 `~/.config/systemd/user/forum-analytics.service`。在该用户的 SSH 会话中运行 `systemctl --user status forum-analytics --no-pager` 查看状态，使用 `journalctl --user -u forum-analytics -n 50 --no-pager` 查看启动错误。代码版本和上传包保留以便排错，不自动删除。部署统计服务不上传网页、不切换旧网站 `current`，不读取或覆盖文章数据库，也不上传本地统计库。

统计数据库不会被采集上传替换，也不属于文章库的滚动备份。服务运行时请使用 SQLite backup API 创建一致的备份，避免直接复制写入中的文件。例如在 ECS 执行以下 Python（备份路径需有写入权限）：

```python
from contextlib import closing
import sqlite3

with closing(sqlite3.connect("/var/www/html/last30days/state/analytics.sqlite3")) as source:
    with closing(sqlite3.connect("/var/www/html/last30days/state/analytics.sqlite3.bak")) as target:
        source.backup(target)
```

本地离线与浏览器验证都使用临时数据库，不访问 ECS 或公开统计接口：

```powershell
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -p test_analytics.py -v
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -p test_analytics_deploy.py -v
.venv/Scripts/python.exe -X utf8 tests/browser_analytics.py
.venv/Scripts/python.exe -X utf8 tests/browser_analytics.py --http-origin
```

浏览器检查包含真实跨域预检/POST、刷新与多标签 UV 去重、新浏览器、禁用存储、预加载不计数、项目路径、普通 HTTP 来源、手机布局、接口离线及非发布来源不计数。
