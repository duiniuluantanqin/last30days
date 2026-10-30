# 拾页 · 讨论归档

一个 Python 论坛主帖采集器与静态阅读网站，支持全文搜索、分页、关键词屏蔽和新文章标记。GitHub Actions 定时采集，网页仅发布到 GitHub Pages，SQLite 数据库保存在 ECS，图片使用原链接。

## 安装与构建

需要 Python 3.11+，从仓库根目录执行。直接生成静态网页，无需前端编译。

Windows：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
.venv/Scripts/python.exe -m forum_archive demo
.venv/Scripts/python.exe -m forum_archive serve --demo --reload --port 8765
```

Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m forum_archive demo
.venv/bin/python -m forum_archive serve --demo --reload --port 8765
```

打开 <http://127.0.0.1:8765>。演示模式使用独立数据，不访问源站。`--reload` 会在保存 Python、模板、CSS/JS、配置或词库后自动重建、重启预览并刷新浏览器；启动时也会重新构建，缺少演示数据时自动生成。若端口已被占用，先在旧预览终端按 Ctrl+C 停止，再启动新命令。

配置入口为 `config.toml`。已有真实采集数据时，重新构建网站：

```powershell
.venv/Scripts/python.exe -m forum_archive build
```

Linux 使用 `.venv/bin/python -m forum_archive build`。详细内容规则、采集命令、测试和部署说明见 [项目维护 skill](.agents/skills/forum-archive-maintainer/SKILL.md)。
