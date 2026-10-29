# 拾页 · 讨论归档

技术讨论区采集器与静态阅读网站。首次补采启动前 **30 天**的主帖，此后由 GitHub Actions **每小时第 17 分钟**运行，按原帖发布时间滚动保留最近 **30 天**。配置在 `config.toml`，没有管理后台。

**按标题和正文黑名单排除整篇帖子，不识别图片内容。图片只保留原链接，采集器不请求、不保存、不上传图片。** 浏览器阅读文章时会直接请求原图地址，外链可能因防盗链、失效或 HTTPS 页面加载 HTTP 图片而无法显示。

主帖正文出现“防水图”时，公开页面屏蔽该文章的全部图片，并从正文和列表摘要中移除“防水图”三个字，保留其余文字，隐藏图片数量和图片加载提示。支持文字跨 HTML 标签或以实体编码出现。该规则也适用于已经采集的文章，重新运行 `build` 即可生效；数据库中的原始归档不变。

正文至少包含 **200 个非空白字符**，且**所有图片都在正文末尾、图片后没有文字**时，公开页面也会屏蔽全部图片，不限制图片数量。空白、换行和图片外层的链接或容器不影响判定；如果正文开头或中间有图片，或图片后有说明文字，则不应用此规则。该规则同样适用于已有归档，隐藏后同步移除图片数量和加载提示，不修改数据库原文。

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
.venv/Scripts/python.exe -m forum_archive crawl --max-posts 3 --recent-pages 1 --backfill-pages 1
.venv/Scripts/python.exe -m forum_archive serve --port 8766
```

打开 <http://127.0.0.1:8766>。预览服务只监听本机；另开终端继续采集后，刷新网页即可看到新的版本。不必重启服务。

继续按配置补采、查看进度：

```powershell
.venv/Scripts/python.exe -m forum_archive crawl
.venv/Scripts/python.exe -m forum_archive status
```

独立演示模式不会访问来源，也不会混入真实数据库：

```powershell
.venv/Scripts/python.exe -m forum_archive demo
.venv/Scripts/python.exe -m forum_archive serve --demo --port 8765
```

## Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m forum_archive crawl --max-posts 3 --recent-pages 1 --backfill-pages 1
.venv/bin/python -m forum_archive serve --port 8766
```

## 本地关键词与来源

关键词统一维护在 `blacklist/keywords.txt`，包括原配置、旧 Block 文件、补充的色情和广告话术，以及原第三方词库中色情、广告两类规则的本地副本。按**标题或正文包含词**匹配，任一命中就屏蔽整篇。

第三方 Git 子模块已移除，本地运行和 GitHub Actions 都直接读取仓库内的词库，不再拉取或自动更新外部词库。新增规则可直接修改 `blacklist/keywords.txt`，也可通过网页 `Block` 提交预填 Issue，由 Actions 自动创建追加关键词的 PR。

迁入来源、版本、标点处理及修改说明见 [blacklist/SOURCES.md](blacklist/SOURCES.md)。原 Apache-2.0 许可证与版权声明保存在 `blacklist/licenses/`。

## 配置与采集行为

- `source.list_url`：指定技术讨论区列表地址。只接受该来源的帖子请求，不跟随重定向到其他地址，不获取其他版块的置顶链接。
- `source.request_interval_seconds`：配置请求间隔，同时遵守 robots.txt 的 Crawl-delay / Request-rate 中更严格的限制。本次验证源站要求每 10 秒至多 1 个请求。
- `crawl.initial_days`：数据库首次建立时固定补采起点；此后还会按每轮运行时间执行最近 30 天保留规则，超过 30 天的帖子不再展示或补采。
- `crawl.recent_pages_per_run`：每轮近期列表扫描预算。第一页每次刷新，其余分页按游标继续扫描，直到最后回复时间越过回看范围后重新开始。
- `crawl.overlap_hours`：近期列表回看时间，默认 48 小时；去重依靠帖子 ID，不依赖标题或一次易变的列表位置。
- `crawl.backfill_pages_per_run`：每轮历史列表扫描预算；断点保存到 ECS 上的状态文件。按最后回复时间判断历史扫描边界；不能因为一页主帖创建日期较旧就提前结束。
- `crawl.max_posts_per_run`：每轮主帖获取预算；发现的合格链接会先保存，再分批获取。默认 60 篇，未完成任务下一轮继续，不会标成已完成。
- `crawl.max_attempts`：主帖最大跨轮重试次数，默认 3；耗尽后仍保留错误记录。人工重试：`python -m forum_archive crawl --retry-failed`。
- `blacklist/keywords.txt`：本地标题或正文包含词，一行一个；会先统一全半角、英文大小写，去掉空白和零宽格式字符。配置中的 `filter.blocked_keywords` 仍兼容读取，当前配置已迁出该列表。
- `filter.blocked_patterns`：分别作用于规范化标题和正文的正则表达式。默认不启用；无效正则会在启动时报告。
- `filter.keyword_files`：可选的额外本地逐行词库文件，路径相对于配置文件目录；当前配置不使用。缺失时报告文件路径，不会静默跳过。
- `site`：网站名称、说明和每页数量。
- `storage`：SQLite 状态与生成网站的目录，路径相对于配置文件所在目录。

先在列表页筛选标题；获取正文后再检查完整标题和全文，不限于摘要。正文使用清理后 HTML 提取出的文字匹配，支持跨标签和 HTML 实体，不匹配标签属性、图片地址或链接目标地址。只提取楼主主帖，不采集楼内回复、头像、签名、视频与附件。清理正文中的脚本、事件属性、表单和嵌入页面。识别 `ess-data` / `data-original` / `data-src` 等原图属性，去掉占位图属性，不发出图片请求。

正文命中的帖子保留在非公开数据库中，以便后续重新应用黑名单，避免每轮重复抓取；不生成公开文章页，仍按 30 天保留规则清理。删除对应规则后，未命中其他规则且未过期的帖子可恢复收录。

修改黑名单后运行 `build` 即可重新筛选现有文章并切换到新的静态版本，不必重新抓取。命中新增规则的旧文章会从首页、归档和文章页中移除，同时删除本地历史生成版本中的对应文章 HTML。发布到 ECS 后也会删除远端 `releases/release-*/posts/<帖子ID>.html` 中的命中页面。帖子 ID、屏蔽状态和数据库归档保留，避免重复收录；移除规则后可以重新生成页面。

```powershell
.venv/Scripts/python.exe -m forum_archive build
```

每次生成网站时，也会清除原帖发布时间早于“当前时间减 30 天”的正文、摘要和图片计数，状态改为 `expired`，删除对应的当前及历史版本文章 HTML，并重新生成列表与月份归档。恰好 30 天的帖子保留；删除使用原帖发布时间，不使用抓取时间、回复时间或文件修改时间。帖子 ID 与时间等元数据保留用于去重，旧帖被回复顶起也不会重新收录。

部署后，只有每小时定时任务会访问源站采集内容；push 和手动任务仅恢复服务器数据库、应用黑名单及 30 天保留规则、重新发布并清理服务器文章页。需要单独执行一次服务器清理时，在 GitHub **Actions → Hourly archive → Run workflow** 中直接运行，无需额外选项。需要先把本次代码推送到默认分支。该流程不要求在本机配置 ECS 凭据，使用仓库现有部署 Secrets。

## 网页划词提交黑名单 PR

在文章标题、正文或列表摘要中用鼠标选中文字，会弹出一个 `Block` 按钮。点击后打开 GitHub 已填好关键词的 Issue 页面：

1. 登录 GitHub，确认选词后提交 Issue，无需编辑 `keywords.txt`，保留自动生成的 Issue 正文。
2. `Block keyword PR` 工作流读取关键词，从仓库默认分支创建 `block/issue-编号` 分支，在 `blacklist/keywords.txt` 末尾自动追加一行并创建 PR。其他内容保持不变；无写权限的读者也可通过 Issue 提议关键词，无需 fork。
3. 仓库作者审核并手动合并 PR 后，新词才进入默认分支（当前为 `master`）并生效；合并时自动关闭关联 Issue。工作流不会自动合并 PR，也不会直接写默认分支。

首次启用须将 `.github/workflows/block-keyword.yml` 和 `scripts/block-keyword-pr.cjs` 推送到仓库默认分支，并确认仓库已开启 **Issues**。在 **Settings → Actions → General → Workflow permissions** 勾选 **Allow GitHub Actions to create and approve pull requests**；本工作流只创建 PR，不调用批准或合并操作。工作流显式申请 Contents / Pull requests 写权限，若组织策略禁止，需要仓库管理员放行。

默认使用 Actions 的 `GITHUB_TOKEN`，无需新增 Secret，令牌不会发送到浏览器或网站。GitHub 不会为这个令牌新建的 PR 自动触发其他工作流；若合并保护要求 PR 自动运行 `test.yml`，可配置可访问本仓库的细粒度 PAT 到 **Actions Secret `BLOCK_PR_TOKEN`**，权限为 Contents、Pull requests 读写，工作流会优先使用它。PR 合并权限仍由仓库权限和分支保护决定；如需强制仅作者合并，应在仓库设置中限制默认分支的更新权限。

重复任务会复用同一个 Issue 分支及 PR，不会重复追加。已被主分支现有词覆盖时不新建 PR，可在 Actions 日志查看原因；API 权限或网络失败时在 Actions 中重跑失败任务即可。私有仓库仅对拥有访问和创建 Issue 权限的用户开放此流程。

新增词统一追加到 `blacklist/keywords.txt`，不再逐词新建文件；原有配置词和旧哈希文件中的词已合并到此文件，加载器仍兼容配置及其他 `blacklist/*.txt`。PR 合并到 `master` 后会触发清理发布，应用新规则及 30 天保留规则、删除命中或过期的旧文章页面，不采集新帖；新内容由下一轮定时任务采集。关键词同时匹配**标题和正文**，已有文章也会按全文重新筛选。

`config.toml` 的 `[site]` 中配置 `github_repository = "owner/repo"`。当前为 `duiniuluantanqin/last30days`；仓库留空会隐藏此功能。PR 目标分支读取 GitHub 仓库实际默认分支，旧配置 `github_branch` 为兼容仍可读取，但不再用于 Block。

选择长度限制为 1–80 个字符，换行合并为空格；已被现有包含词规则覆盖、包含控制字符或超长的选词不显示按钮，工作流也会独立校验输入。点击空白处、滚动或按 Escape 可关闭。普通 HTTP（包括公网 IP 地址）和 HTTPS 都支持，不依赖剪贴板权限、Web Crypto 或网站后端。

**已有 Caddy 配置也需要同步更新 CSP：** 在 `Content-Security-Policy` 中增加 `script-src 'self'`，参考 `deploy/Caddyfile.example`。这里只允许加载本网站的脚本，不需要放开内联脚本或 GitHub API 请求。修改 Caddy 配置后验证并重载；本地预览服务修改代码后也应重启。

`backfill_complete` 表示历史列表发现阶段已完成，不代表所有正文都已取得。应同时检查 `pending` 数量和错误记录。列表变动、来源访问限制及删除的原帖都可能影响覆盖范围；不会宣称恢复不可访问的内容。现阶段保存首次取得的正文，不自动追踪原帖之后的编辑和删除。

## 文件与数据

```text
config.toml                      来源、黑名单与运行预算
blacklist/keywords.txt            通过 Block PR 集中追加的黑名单关键词
blacklist/SOURCES.md             迁入词库的来源、版本及修改说明
blacklist/licenses/              迁入词库的许可证与版权声明
forum_archive/                   采集、状态、渲染与本地预览
forum_archive/templates/         阅读页模板
forum_archive/static/            仅 CSS，无图片资源下载
data/archive.sqlite3             真实采集数据和断点（不提交 Git）
data/demo/archive.sqlite3        独立演示数据（不提交 Git）
site/release-*/                  生成的公开网页（不提交 Git）
site/current.json                本地当前版本指针
scripts/remote.py                可选的 ECS 状态恢复与发布
deploy/Caddyfile.example         新域名站点配置示例
AGENTS.md                       项目维护约束、代码入口和验证命令
.agents/skills/forum-archive-maintainer/SKILL.md  项目专用维护技能
tests/                          离线回归测试，不访问源站
```

SQLite 包含结构化正文与状态，是权威归档，需要备份。公开版本只包含网页与 CSS，不包含数据库、日志、密钥和下载的图片。每次构建到新目录，完成后原子切换版本指针，失败不会覆盖旧站点。

## 测试

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
node --test tests/test_block_keyword_pr.cjs
```

覆盖标题和全文规则、正文命中后的持续屏蔽、真实页面结构的时间戳、来源版块限制、主帖与回复分离、正文清理、懒加载原图链接、零图片请求、去重、失败重试、历史扫描边界、robots 限速和重新筛选后的旧页面下线。

Issue 转 PR 测试需要 Node.js 20 或更高版本，使用模拟 GitHub API，覆盖追加写入、重复词、不合法输入、任务重跑和失败恢复，不实际创建 Issue 或 PR。

可选浏览器检查需要在执行该命令的 Python 环境中安装 Playwright 和可用的 Chromium/Chrome。先启动演示预览，再运行 `python tests/browser_keyword_menu.py --url http://127.0.0.1:8765/`。检查实际鼠标划词、预填 Issue 链接和关键词载荷、重复词、长度限制和手机弹窗位置，不向 GitHub 提交任何内容。

追加 `--http-origin` 可将本地页面映射到普通 HTTP 测试来源，验证 Chrome 中 `isSecureContext=false` 时的 `Block` 菜单及 Issue 跳转；同时校验中文、emoji 和特殊字符的编码。

## GitHub Actions 与 ECS（本地测试不会执行部署）

`test.yml` 在 push/PR 执行离线测试。`archive.yml` 在 `master` 分支每次 push 后只清理并发布已有内容（包括合并 PR）；手动触发也只清理发布。仅默认分支的每小时定时任务执行源站采集。定时任务可能延迟，不承诺严格准点。更换默认分支时同步修改工作流的 `push.branches`。一次 push 包含多个 commit 时执行一次。

启用线上任务前，需要在 Ubuntu ECS 上准备独立网站目录和**非 root 部署用户**，使其可写 `/var/www/html/last30days`；Caddy 用户需要能读取公开的 `current` 版本。新域名解析到 ECS。将 `deploy/Caddyfile.example` 的站点块追加到现有配置，替换域名后验证并重载，不能直接覆盖现有网站的整份配置。

在 GitHub 仓库配置这些 Secrets：

| Secret | 内容 |
|---|---|
| `DEPLOY_HOST` | ECS 域名或公网 IPv4 |
| `DEPLOY_USER` | 独立的非 root 部署用户 |
| `DEPLOY_SSH_KEY` | 专用 SSH 私钥，仅供该用户登录 |
| `DEPLOY_KNOWN_HOSTS` | 通过可信方式核验的 ECS SSH 主机公钥记录；自定义端口应使用对应记录格式 |

可选 Repository Variables：`DEPLOY_ROOT`（默认 `/var/www/html/last30days`）、`DEPLOY_PORT`（默认 `22`）。服务器端目录结构：

```text
/var/www/html/last30days/
  current -> releases/release-...    Caddy 唯一公开根目录
  releases/                         最新 3 个版本，额外保护 current 指向的版本
  state/archive.sqlite3             权威数据与进度，不公开
  state/archive.sqlite3.bak         替换前的上一份数据库，不公开
  incoming/                         发布暂存包，不公开
```

push 和手动任务顺序：恢复 ECS 状态 → 清理黑名单与超过 30 天的已有帖子 → 生成并发布静态版本、切换网站入口、保存新状态 → 删除命中和过期文章的历史 HTML。定时任务在完成上述清理发布后，继续限速采集 → 重新生成并发布网站及状态。所有发布任务互斥，使用严格的 SSH 主机指纹校验，不把 Actions 缓存当数据库。

每次发布会先将服务器原有数据库复制到暂存文件，再原子替换 `state/archive.sqlite3.bak`，随后切换网站及当前数据库。备份只保留上一份，首次发布没有旧数据库时不创建备份；备份失败会在切换网站前停止。该备份与站点位于同一磁盘，用于回退，不能替代异地备份。

发布与文章下架成功后，`scripts/prune-deployments.sh` 自动执行以下清理：

- `releases/` 按发布编号保留最新 **3 个**生成版本，同时始终保护 `current` 的实际目标。如果手动回退到了更老的版本，该版本额外保留，此时最多有 4 个生成版本。
- 删除本次成功发布的压缩包、暂存数据库、删除清单及清理脚本；清理 `incoming/` 中超过 **24 小时**的已知发布文件，包括失败时留下的数据库备份暂存文件。最近 24 小时内的其他上传保留，无关文件、子目录和软链接不遍历。
- 清理不会删除 `state/`；当前链接失效、指向部署目录外部，或发布目录/上传目录为软链接时停止清理。清理在成功发布后执行，失败残留会在后续成功发布时处理。

个别主帖失败时仍会发布成功内容并保存重试状态，然后标记工作流失败供查看。列表解析失败时不发布该轮新增内容，采集前已完成的黑名单及过期文章下架继续生效；SSH 发布失败则报告错误并保留已有可访问版本。CLI 退出码：`0` 成功，`1` 关键错误，`2` 部分主帖失败。

**费用与磁盘：** 源站的 10 秒请求间隔使默认 60 篇上限的一轮任务可能运行十余分钟。每小时运行请先测量实际时长，再按 GitHub 当前套餐评估，不能假设私有仓库免费额度足够。历史网站版本及上传残留按上述规则自动清理。SQLite 会清除过期正文并保留去重元数据；数据库文件不会因此立即缩小，元数据仍会随采集增加。备份及近期上传包可能含有原始内容，不能将它们公开作为网站根目录。

本项目不包含登录、验证码绕过或访问限制规避。若 GitHub 运行环境无法正常访问来源，工作流会报告失败，需要重新评估执行环境。
