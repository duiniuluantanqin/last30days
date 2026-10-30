# 黑名单关键词

统一词库为 `blacklist.txt`，每行一个关键词，匹配标题和完整正文；任一命中就屏蔽整篇。不要添加分类标题或注释行，它们也会成为关键词。

网页划词点击 `Block`，会打开 GitHub 新建文件页，预填独立的 `block-时间戳-随机标识.txt` 和关键词。检查后提交即可，不需要编辑统一词库。

文件进入默认分支后，Hourly archive 自动运行 `scripts/merge_blacklist.py`，将本目录其他 `.txt` 去重合并到 `blacklist.txt`，删除已合并文件并提交结果，再清理和发布网站。原有 `keywords.txt` 已迁入统一词库。按现有规范化规则去重，保留首次写法与标点；不读取子目录，所有输入读取成功后才写入或删除。

流水线需要 `contents: write` 并允许机器人向默认分支提交。不创建 Issue 或自动 PR，浏览器不保存令牌。详细操作见 [内容规则](../.agents/skills/forum-archive-maintainer/references/rules.md#网页划词提交黑名单)。
