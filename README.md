# 拾页 · 讨论归档

技术讨论区采集器与静态阅读网站。首次补采启动前 **30 天**的主帖，此后由 GitHub Actions **每小时第 17 分钟**运行，按原帖发布时间滚动保留最近 **30 天**。配置在 `config.toml`，没有管理后台。

**只按标题黑名单排除帖子，不检查正文或图片是否色情。图片只保留原链接，采集器不请求、不保存、不上传图片。** 浏览器阅读文章时会直接请求原图地址，外链可能因防盗链、失效或 HTTPS 页面加载 HTTP 图片而无法显示。

主帖正文出现“防水图”时，公开页面屏蔽该文章的全部图片，保留文字，并隐藏图片数量和图片加载提示。该规则也适用于已经采集的文章，重新运行 `build` 即可生效；数据库中的原始归档不变。

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

## 配置与采集行为

- `source.list_url`：指定技术讨论区列表地址。只接受该来源的帖子请求，不跟随重定向到其他地址，不获取其他版块的置顶链接。
- `source.request_interval_seconds`：配置请求间隔，同时遵守 robots.txt 的 Crawl-delay / Request-rate 中更严格的限制。本次验证源站要求每 10 秒至多 1 个请求。
- `crawl.initial_days`：数据库首次建立时固定补采起点；此后还会按每轮运行时间执行最近 30 天保留规则，超过 30 天的帖子不再展示或补采。
- `crawl.recent_pages_per_run`：每轮近期列表扫描预算。第一页每次刷新，其余分页按游标继续扫描，直到最后回复时间越过回看范围后重新开始。
- `crawl.overlap_hours`：近期列表回看时间，默认 48 小时；去重依靠帖子 ID，不依赖标题或一次易变的列表位置。
- `crawl.backfill_pages_per_run`：每轮历史列表扫描预算；断点保存到 ECS 上的状态文件。按最后回复时间判断历史扫描边界；不能因为一页主帖创建日期较旧就提前结束。
- `crawl.max_posts_per_run`：每轮主帖获取预算；发现的合格链接会先保存，再分批获取。默认 60 篇，未完成任务下一轮继续，不会标成已完成。
- `crawl.max_attempts`：主帖最大跨轮重试次数，默认 3；耗尽后仍保留错误记录。人工重试：`python -m forum_archive crawl --retry-failed`。
- `filter.blocked_keywords`：标题包含词；会先统一全半角、英文大小写，去掉空白和零宽格式字符。
- `filter.blocked_patterns`：作用于规范化标题的正则表达式。默认不启用；无效正则会在启动时报告。
- `site`：网站名称、说明和每页数量。
- `storage`：SQLite 状态与生成网站的目录，路径相对于配置文件所在目录。

先在列表页筛选标题；获取正文后再用完整标题复查。只提取楼主主帖，不采集楼内回复、头像、签名、视频与附件。清理正文中的脚本、事件属性、表单和嵌入页面。识别 `ess-data` / `data-original` / `data-src` 等原图属性，去掉占位图属性，不发出图片请求。

修改黑名单后运行 `build` 即可重新筛选现有文章并切换到新的静态版本，不必重新抓取。命中新增规则的旧文章会从首页、归档和文章页中移除，同时删除本地历史生成版本中的对应文章 HTML。发布到 ECS 后也会删除远端 `releases/release-*/posts/<帖子ID>.html` 中的命中页面。帖子 ID、屏蔽状态和数据库归档保留，避免重复收录；移除规则后可以重新生成页面。

```powershell
.venv/Scripts/python.exe -m forum_archive build
```

每次生成网站时，也会清除原帖发布时间早于“当前时间减 30 天”的正文、摘要和图片计数，状态改为 `expired`，删除对应的当前及历史版本文章 HTML，并重新生成列表与月份归档。恰好 30 天的帖子保留；删除使用原帖发布时间，不使用抓取时间、回复时间或文件修改时间。帖子 ID 与时间等元数据保留用于去重，旧帖被回复顶起也不会重新收录。

部署后，每小时任务、push 和手动任务都会先完成清理发布，再访问源站。需要单独执行一次服务器清理时，在 GitHub **Actions → Hourly archive → Run workflow** 中勾选 `cleanup_only`；它会恢复服务器数据库、应用黑名单及 30 天保留规则、发布并清理服务器文章页，不抓取新帖。需要先把本次代码推送到默认分支。该流程不要求在本机配置 ECS 凭据，使用仓库现有部署 Secrets。

## 网页划词提交黑名单 PR

在文章标题、正文或列表摘要中用鼠标选中文字，会弹出一个 `Block` 按钮。点击后，在 GitHub 登录并确认：

1. 检查预填的关键词文件内容。
2. 选择 **Create a new branch for this commit and start a pull request**，创建新分支，不要直接提交到默认分支。
3. 按 GitHub 页面提示创建 Pull Request。没有仓库写权限的读者需要能访问该仓库，并按提示 fork 后提交。

菜单只准备 GitHub 链接，不会在网站内登录或保存令牌，也不会自动提交、合并 PR。私有仓库仅对拥有访问权限的用户开放此流程。

新增词保存在 `blacklist/<关键词 SHA-256>.txt`，原有 `config.toml` 黑名单继续有效。PR 合并到 `master` 后会触发流水线，先应用新规则及 30 天保留规则并发布、删除命中或过期的旧文章页面，再采集新帖子；源站抓取失败不会撤销已完成的下架。关键词匹配的是**标题**，在正文中选词不代表按正文屏蔽文章。

`config.toml` 的 `[site]` 中配置 `github_repository = "owner/repo"` 和 `github_branch = "默认分支名"`。当前已按仓库 origin 配置为 `duiniuluantanqin/last30days`、`master`；更换仓库或分支时同步修改。仓库留空会隐藏此功能。

选择长度限制为 1–80 个字符，换行合并为空格；已被现有包含词规则覆盖、超长或不支持的选词不显示按钮。点击空白处、滚动或按 Escape 可关闭。网页正式部署需要 HTTPS（本地 localhost/127.0.0.1 也可用）。

**已有 Caddy 配置也需要同步更新 CSP：** 在 `Content-Security-Policy` 中增加 `script-src 'self'`，参考 `deploy/Caddyfile.example`。这里只允许加载本网站的脚本，不需要放开内联脚本或 GitHub API 请求。修改 Caddy 配置后验证并重载；本地预览服务修改代码后也应重启。

`backfill_complete` 表示历史列表发现阶段已完成，不代表所有正文都已取得。应同时检查 `pending` 数量和错误记录。列表变动、来源访问限制及删除的原帖都可能影响覆盖范围；不会宣称恢复不可访问的内容。现阶段保存首次取得的正文，不自动追踪原帖之后的编辑和删除。

## 文件与数据

```text
config.toml                      来源、黑名单与运行预算
blacklist/*.txt                   通过 PR 增加的黑名单关键词
forum_archive/                   采集、状态、渲染与本地预览
forum_archive/templates/         阅读页模板
forum_archive/static/            仅 CSS，无图片资源下载
data/archive.sqlite3             真实采集数据和断点（不提交 Git）
data/demo/archive.sqlite3        独立演示数据（不提交 Git）
site/release-*/                  生成的公开网页（不提交 Git）
site/current.json                本地当前版本指针
scripts/remote.py                可选的 ECS 状态恢复与发布
deploy/Caddyfile.example         新域名站点配置示例
tests/                          离线回归测试，不访问源站
```

SQLite 包含结构化正文与状态，是权威归档，需要备份。公开版本只包含网页与 CSS，不包含数据库、日志、密钥和下载的图片。每次构建到新目录，完成后原子切换版本指针，失败不会覆盖旧站点。

## 测试

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

覆盖标题规则、真实页面结构的时间戳、来源版块限制、主帖与回复分离、正文清理、懒加载原图链接、零图片请求、去重、失败重试、历史扫描边界、robots 限速和重新筛选后的旧页面下线。

可选浏览器检查需要在执行该命令的 Python 环境中安装 Playwright 和可用的 Chromium/Chrome。先启动演示预览，再运行 `python tests/browser_keyword_menu.py --url http://127.0.0.1:8765/`。检查实际鼠标划词、GitHub 链接、重复词、长度限制和手机弹窗位置，不向 GitHub 提交任何内容。

## GitHub Actions 与 ECS（本地测试不会执行部署）

`test.yml` 在 push/PR 执行离线测试。`archive.yml` 在 `master` 分支每次 push 后执行采集发布（包括合并 PR），同时保留默认分支每小时运行和手动触发；定时任务可能延迟，不承诺严格准点。更换默认分支时同步修改工作流的 `push.branches`。一次 push 包含多个 commit 时执行一次。

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
  releases/                         生成的网站版本
  state/archive.sqlite3             权威数据与进度，不公开
  incoming/                         发布暂存包，不公开
```

任务顺序：恢复 ECS 状态 → 清理黑名单与超过 30 天的已有帖子并发布 → 限速采集 → 生成静态版本 → 完整上传 → 切换网站入口 → 提交新状态 → 删除黑名单及过期文章的历史 HTML。`cleanup_only` 模式在首次清理发布后结束。使用严格的 SSH 主机指纹校验。定时与手动任务互斥，不把 Actions 缓存当数据库。

个别主帖失败时仍会发布成功内容并保存重试状态，然后标记工作流失败供查看。列表解析失败时不发布该轮新增内容，采集前已完成的黑名单及过期文章下架继续生效；SSH 发布失败则报告错误并保留已有可访问版本。CLI 退出码：`0` 成功，`1` 关键错误，`2` 部分主帖失败。

**费用与磁盘：** 源站的 10 秒请求间隔使默认 60 篇上限的一轮任务可能运行十余分钟。每小时运行请先测量实际时长，再按 GitHub 当前套餐评估，不能假设私有仓库免费额度足够。现版本自动删除黑名单及过期文章的历史 HTML，并清除过期数据库正文；不删除整个旧发布目录和暂存压缩包，长期运行前需安排这些生成副本的保留策略，不能无限积累。旧压缩包和备份仍可能含有原始内容，不能将它们公开作为网站根目录。

本项目不包含登录、验证码绕过或访问限制规避。若 GitHub 运行环境无法正常访问来源，工作流会报告失败，需要重新评估执行环境。
