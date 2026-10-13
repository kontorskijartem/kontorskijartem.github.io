#!/usr/bin/env python3
"""Собирает статический сайт и RSS-ленту для Дзена из markdown-файлов.

    python build.py                       # articles/ -> public/
    python build.py --articles examples --out /tmp/out
"""
import argparse
import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

import markdown

ROOT = Path(__file__).parent


def parse_article(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        sys.exit(f"{path.name}: нет блока front matter (--- ... ---) в начале файла")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    for key in ("title", "date"):
        if not meta.get(key):
            sys.exit(f"{path.name}: в front matter не задано поле '{key}'")
    try:
        when = datetime.fromisoformat(meta["date"])
    except ValueError:
        sys.exit(f"{path.name}: дата '{meta['date']}' должна быть в формате 2026-10-13T12:00:00+03:00")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    meta["when"] = when
    meta["slug"] = path.stem
    meta["body_md"] = m.group(2).strip()
    return meta


def absolutize(fragment, base):
    return re.sub(r'(src|href)="(/[^"]*)"', lambda mm: f'{mm.group(1)}="{base}{mm.group(2)}"', fragment)


def to_html(md_text):
    out = markdown.markdown(md_text, extensions=["extra", "sane_lists"])
    return re.sub(r"<p>(<img [^>]*>)</p>", r"<figure>\1</figure>", out)


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
{head_extra}<style>body{{font:18px/1.6 Georgia,serif;max-width:720px;margin:2rem auto;padding:0 1rem;color:#222}}
img{{max-width:100%;height:auto}}h1{{line-height:1.25}}a{{color:#0b57d0}}time{{color:#666;font-size:.85em}}</style>
</head><body>{body}</body></html>"""


def build(articles_dir, out_dir, cfg, base):
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "articles").mkdir(parents=True)
    static = ROOT / "static"
    if static.exists():
        for f in static.iterdir():
            if f.is_file() and f.name != ".gitkeep":
                shutil.copy(f, out / f.name)
    (out / ".nojekyll").write_text("")
    token = cfg.get("zen_verification", "")
    if token:
        (out / f"zen_{token}.html").write_text(
            f'<meta name="zen-verification" content="{token}" />\n', encoding="utf-8")
        cfg["_verify_meta"] = f'<meta name="zen-verification" content="{token}">\n'

    items = sorted(
        (parse_article(p) for p in Path(articles_dir).glob("*.md")),
        key=lambda a: a["when"], reverse=True,
    )

    for a in items:
        a["html"] = to_html(a["body_md"])
        a["url"] = f"{base}/articles/{a['slug']}.html"
        body = (f'<p><a href="{base}/">← {html.escape(cfg["title"])}</a></p>'
                f'<h1>{html.escape(a["title"])}</h1>'
                f'<time datetime="{a["when"].isoformat()}">{a["when"].strftime("%d.%m.%Y")}</time>'
                f'{a["html"]}')
        desc = html.escape(a.get("description", ""), quote=True)
        (out / "articles" / f"{a['slug']}.html").write_text(
            PAGE.format(title=html.escape(a["title"]), description=desc,
                        head_extra=cfg.get("_verify_meta", ""), body=body),
            encoding="utf-8")

    links = "".join(
        f'<li><a href="{a["url"]}">{html.escape(a["title"])}</a> <time>{a["when"].strftime("%d.%m.%Y")}</time></li>'
        for a in items)
    head = (cfg.get("_verify_meta", "")
            + f'<link rel="alternate" type="application/rss+xml" title="RSS" href="{base}/rss.xml">\n')
    (out / "index.html").write_text(
        PAGE.format(title=html.escape(cfg["title"]), description=html.escape(cfg["description"], quote=True),
                    head_extra=head,
                    body=f'<h1>{html.escape(cfg["title"])}</h1><p>{html.escape(cfg["description"])}</p><ul>{links}</ul>'),
        encoding="utf-8")

    (out / "rss.xml").write_text(rss(items, cfg, base), encoding="utf-8")
    return items


def rss(items, cfg, base):
    d = cfg["defaults"]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
             'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:media="http://search.yahoo.com/mrss/" '
             'xmlns:atom="http://www.w3.org/2005/Atom">',
             "<channel>",
             f"<title>{html.escape(cfg['title'])}</title>",
             f"<link>{base}/</link>",
             f"<description>{html.escape(cfg['description'])}</description>",
             "<language>ru</language>",
             f'<atom:link href="{base}/rss.xml" rel="self" type="application/rss+xml"/>']
    for a in items:
        draft = a.get("draft", str(d["draft"])).lower() in ("true", "1", "yes", "да")
        cats = (["native-draft"] if draft else []) + [
            a.get("format", d["format"]), a.get("index", d["index"]), a.get("comments", d["comments"])]
        content = absolutize(a["html"], base)
        parts += ["<item>",
                  f"<title>{html.escape(a['title'])}</title>",
                  f"<link>{a['url']}</link>",
                  f"<guid>{a['url']}</guid>",
                  f"<pubDate>{format_datetime(a['when'])}</pubDate>"]
        parts += [f"<category>{c}</category>" for c in cats]
        if a.get("description"):
            parts.append(f"<description><![CDATA[{a['description']}]]></description>")
        if a.get("image"):
            img = a["image"] if a["image"].startswith("http") else f"{base}/{a['image'].lstrip('/')}"
            parts.append(f'<enclosure url="{html.escape(img, quote=True)}" type="image/jpeg"/>')
        parts += [f"<content:encoded><![CDATA[{content}]]></content:encoded>", "</item>"]
    parts += ["</channel>", "</rss>"]
    return "\n".join(parts) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--articles", default=str(ROOT / "articles"))
    ap.add_argument("--out", default=str(ROOT / "public"))
    ap.add_argument("--base-url", default=None, help="перекрывает base_url из site.json")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))
    base = (args.base_url or cfg["base_url"]).rstrip("/")
    items = build(args.articles, args.out, cfg, base)
    print(f"Готово: {len(items)} статей -> {args.out}  (лента: {base}/rss.xml)")


if __name__ == "__main__":
    main()
