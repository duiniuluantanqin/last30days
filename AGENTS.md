# 项目维护说明

本项目是 Python 论坛主帖采集器和 Jinja 静态阅读站，使用 SQLite 保存权威状态，通过 GitHub Actions 发布到 Ubuntu ECS，复用 Caddy。产品行为与部署步骤见 `README.md`。

## 工作方式

- 修改前说明影响正确性的假设；不明确的规则先确认，小歧义采用最小可逆方案。
- 只修改本次任务需要的代码，保留用户已有修改，不顺手重构、升级依赖或格式化整个仓库。
- 对行为变化优先增加可复现的回归测试，再实施最小修复；验证后报告结果及尚未验证的范围。
- 本地测试不等于线上发布。提交、推送、操作服务器及生产 Caddy 配置须在用户授权范围内进行；不暴露部署凭据。

## 关键约束

- 只采集楼主主帖；不收集回复、视频、附件，不下载图片，只保存原图 URL。
- 黑名单匹配标题与完整正文文字，任一命中屏蔽整篇；不匹配 HTML 属性、图片 URL 或链接目标。
- `Block` 保持单按钮、纯静态：复制关键词并打开 GitHub 的 `blacklist/keywords.txt` 编辑页，由用户追加一行并创建 PR，不保存令牌。
- 图片展示规则在构建时处理，适用于已有归档；不覆写 SQLite 中的原始正文。修改规则时同步处理图片计数、提示和摘要。
- “防水图”命中时隐藏全部图片，并移除正文及摘要中的这三个字；正文至少 200 个非空白字符且所有图片只出现在文末时，也隐藏全部图片，不限张数。正文中间配图或图片后有文字时不应用后一规则。
- 按原帖发布时间滚动保留 30 天。帖子 ID 和状态用于去重；不要用文件修改时间、抓取时间或最后回复时间代替发布时间。
- 发布链路先恢复 ECS 状态、应用黑名单和保留规则并发布，再采集。命中或过期 ID 经私有清单删除历史版本的对应文章 HTML；清理范围不能扩大到其他网站或任意路径。
- 关键词统一维护在 `blacklist/keywords.txt`，已迁入原上游色情和广告分类的规则；无 Git 子模块或自动上游更新依赖。迁入词库的来源和许可证见 `blacklist/SOURCES.md`。

## 代码入口与验证

- `forum_archive/source.py`：页面解析、主帖提取、HTML 清理。
- `forum_archive/config.py`、`store.py`、`crawler.py`：规则加载、历史重筛、去重和采集。
- `forum_archive/site.py`、`templates/`、`static/`：构建、图片展示、本地预览及 Block 菜单。
- `scripts/remote.py`、`scripts/purge-blocked.sh`、`.github/workflows/`：发布和服务器旧页面删除。
- 维护这些功能时参考项目技能：[forum-archive-maintainer](.agents/skills/forum-archive-maintainer/SKILL.md)。

Windows 离线测试：`.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -v`。
演示构建：`.venv/Scripts/python.exe -m forum_archive demo`，使用独立的演示数据库，不访问源站。
前端行为变更时按 README 的可选浏览器检查验证，包括普通公网 HTTP 来源；不在测试中提交 GitHub PR。
