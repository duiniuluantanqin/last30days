import argparse
from dataclasses import replace
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import httpx

from .config import Settings, Source
from .crawler import crawl
from .site import build, serve
from .source import Post, Thread
from .store import Store


def demo_settings(settings: Settings) -> Settings:
    return replace(settings, source=Source("https://example.com/thread0806.php?fid=7"),
                   site=replace(settings.site, subtitle="本地演示 · 非真实采集", description="用于检查排版的本地示例内容。", page_size=5),
                   database=settings.root / "data/demo/archive.sqlite3", output=settings.root / "site/demo")


def create_demo(settings: Settings):
    now = datetime.now(ZoneInfo(settings.source.timezone))
    examples = [
        ("把旧电脑变成一台安静的家庭服务器", "从功耗、硬盘到日常维护，记录一次家庭服务器的整理过程。", "<h2>先从实际需求开始</h2><p>文件备份、照片整理和几个轻量服务，是这台电脑的新工作。比起一口气安装很多软件，先让备份稳定运行更重要。</p><blockquote>先把数据放好，再考虑更多功能。</blockquote><p>系统更新、磁盘健康检查和定期恢复测试，都需要留出时间。</p>"),
        ("最近读到的一本书，以及几个还没有想明白的问题", "记下阅读时停留最久的段落，也聊聊不同的理解。", "<p>有些书适合快速读完，有些书适合放在手边慢慢翻。这次的阅读记录，只记下几个值得继续想的问题。</p><p>好的讨论往往从不同意见开始。</p>"),
        ("第一次用 Caddy 部署个人网站的记录", "一个域名、一台服务器，以及一次尽量简单的部署。", "<p>先确认域名解析，再检查服务和 HTTPS。每次只验证一个环节，排错会容易很多。</p><pre><code>域名解析 → 网站目录 → HTTPS → 访问验证</code></pre><p>把部署步骤写下来，下次迁移时就能节省不少时间。</p>"),
        ("周末散步，发现街角开了一间新的小店", "走熟悉的路，也会遇到一点新的变化。", "<p>换个时间出门，同一条街也会有不一样的样子。店里的座位不多，窗边正好能看见路口。</p>"),
        ("每个月整理一次数字文件，真的有必要吗？", "聊聊照片、下载目录与总是舍不得删除的文件。", "<p>不用一次整理完所有文件。先把新的文件放到固定位置，再慢慢归档旧内容。</p><ul><li>保留一份独立备份</li><li>使用能理解的文件名</li><li>定期检查备份是否可恢复</li></ul>"),
        ("一个不用安装应用的简单记账办法", "简单的方法，往往更容易坚持下去。", "<p>从记录日期、类别和金额开始。一周回顾一次，比反复调整工具更有用。</p>"),
        ("原图链接保留示例", "采集器保存图片 URL，不下载图片；浏览器打开正文时才请求原地址。", "<p>下面是用于验证链接保留的示例地址，不代表一张可用图片。</p><img src=\"https://example.com/original/photo.jpg\" alt=\"演示图片地址，可能无法加载\" loading=\"lazy\" referrerpolicy=\"no-referrer\"><p>示例图片不会写入本地或上传服务器。</p>"),
        ("你是怎样安排每周的阅读时间的？", "不追求读得更多，只希望能记住一点。", "<p>把想读的文章存下来很容易，真正留出时间阅读却需要一点安排。</p>"),
    ]
    with Store(settings, now) as store:
        for index, (title, summary, body) in enumerate(examples, 1):
            date = now - timedelta(hours=index * 7)
            thread_id = str(900000 + index)
            store.discover(Thread(thread_id, f"https://example.com/htm_data/2610/7/{thread_id}.html", title, date.isoformat()), now)
            store.save_post(thread_id, Post(title, date.isoformat(), body, summary, int("<img" in body)), now, False)
        store.set("last_success_at", now.isoformat())
    return build(settings)


def parser():
    result = argparse.ArgumentParser(description="标题黑名单采集、静态网站生成与本地预览（不下载图片）")
    result.add_argument("--config", default="config.toml", help="配置文件路径")
    commands = result.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("crawl", help="采集并生成网站")
    collect.add_argument("--max-posts", type=int, help="本次最多获取的主帖数")
    collect.add_argument("--recent-pages", type=int, help="本次最近列表扫描页数")
    collect.add_argument("--backfill-pages", type=int, help="本次历史列表扫描页数")
    collect.add_argument("--retry-failed", action="store_true", help="重置已耗尽的正文重试次数")
    commands.add_parser("demo", help="生成独立演示数据，不访问来源站点")
    for name, help_text in (("build", "从已保存内容重新生成网站"), ("status", "查看采集进度"), ("serve", "启动本地预览")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--demo", action="store_true", help="使用独立演示数据")
        if name == "build":
            command.add_argument("--path-file", help="将生成目录写到指定文件，供自动部署使用")
        if name == "serve":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8000)
    return result


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parser().parse_args(argv)
    try:
        settings = Settings.load(args.config)
        if getattr(args, "demo", False) or args.command == "demo":
            settings = demo_settings(settings)
        if args.command == "crawl":
            for flag, field in (("max_posts", "max_posts_per_run"), ("recent_pages", "recent_pages_per_run"), ("backfill_pages", "backfill_pages_per_run")):
                value = getattr(args, flag)
                if value is not None:
                    if value < 1:
                        raise ValueError(f"--{flag.replace('_', '-')} 必须大于 0")
                    setattr(settings.crawl, field, value)
            stats = crawl(settings, retry_failed=args.retry_failed)
            release = build(settings)
            print(json.dumps(stats, ensure_ascii=False, indent=2))
            print(f"网站已生成：{release}")
            return 2 if stats["errors"] else 0
        if args.command in {"build", "demo"}:
            release = create_demo(settings) if args.command == "demo" else build(settings)
            if getattr(args, "path_file", None):
                Path(args.path_file).write_text(str(release.resolve()), encoding="utf-8")
            print(f"网站已生成：{release}")
        elif args.command == "serve":
            serve(settings, args.host, args.port)
        elif args.command == "status":
            with Store(settings, datetime.now(ZoneInfo(settings.source.timezone))) as store:
                status = {"counts": store.counts(), "archive_start": store.get("archive_start"),
                          "last_scan_at": store.get("last_scan_at"), "last_success_at": store.get("last_success_at"),
                          "backfill_page": store.get("backfill_page"), "backfill_complete": store.get("backfill_complete") == "true",
                          "failures": [dict(row) for row in store.db.execute("SELECT id,attempts,last_error FROM threads WHERE status='pending' AND last_error IS NOT NULL")]}
            print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as error:
        logging.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
