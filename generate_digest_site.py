import base64
import json
import os
import re
import sys
from datetime import UTC, datetime
from html import escape, unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote

import feedparser
import requests
from google import genai
from google.genai import types


ROOT = Path(__file__).parent
DOCS_DIR = ROOT / "docs"
DATA_FILE = DOCS_DIR / "digest.json"
INDEX_FILE = DOCS_DIR / "index.html"
NOJEKYLL_FILE = DOCS_DIR / ".nojekyll"

DEFAULT_MODEL = "gemini-2.5-flash-lite"
DEFAULT_TIMEOUT = 20
DEFAULT_TOTAL_ITEMS = 20
DEFAULT_ITEMS_PER_SOURCE = 5

SOURCES = [
    {
        "name": "TechCrunch AI",
        "feed_url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "kind": "media",
    },
    {
        "name": "VentureBeat AI",
        "feed_url": "https://venturebeat.com/ai/feed/",
        "kind": "media",
    },
    {
        "name": "OpenAI Blog",
        "feed_url": "https://openai.com/news/rss.xml",
        "kind": "media",
    },
    {
        "name": "Hugging Face Blog",
        "feed_url": "https://huggingface.co/blog/feed.xml",
        "kind": "media",
    },
    {
        "name": "arXiv cs.AI",
        "feed_url": "https://export.arxiv.org/rss/cs.AI",
        "kind": "research",
    },
    {
        "name": "arXiv cs.LG",
        "feed_url": "https://export.arxiv.org/rss/cs.LG",
        "kind": "research",
    },
]


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def getenv_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc


def strip_html(value: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(no_tags)).strip()


def iso_from_struct_time(struct_time: Any) -> str:
    if not struct_time:
        return ""
    return datetime(*struct_time[:6], tzinfo=UTC).isoformat()


def short_date(iso_value: str) -> str:
    if not iso_value:
        return "Unknown"
    try:
        return datetime.fromisoformat(iso_value).strftime("%Y-%m-%d")
    except ValueError:
        return iso_value[:10]


def extract_image(entry: Any) -> str:
    media_content = entry.get("media_content") or []
    for item in media_content:
        url = item.get("url")
        if url:
            return url

    media_thumbnail = entry.get("media_thumbnail") or []
    for item in media_thumbnail:
        url = item.get("url")
        if url:
            return url

    summary = entry.get("summary") or entry.get("description") or ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, flags=re.IGNORECASE)
    if match:
        return match.group(1)

    for link in entry.get("links", []):
        href = link.get("href")
        link_type = link.get("type") or ""
        if href and link_type.startswith("image/"):
            return href

    return ""


def make_placeholder_image(source: str, title: str) -> str:
    safe_source = escape(source)
    safe_title = escape(title[:76] + ("..." if len(title) > 76 else ""))
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675">
      <defs>
        <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#07111f"/>
          <stop offset="50%" stop-color="#10314b"/>
          <stop offset="100%" stop-color="#0d5b6b"/>
        </linearGradient>
      </defs>
      <rect width="1200" height="675" fill="url(#bg)"/>
      <circle cx="1020" cy="120" r="180" fill="rgba(98, 209, 255, 0.18)"/>
      <circle cx="180" cy="570" r="220" fill="rgba(255, 184, 77, 0.12)"/>
      <text x="72" y="112" fill="#7de2ff" font-size="30" font-family="Arial, sans-serif" letter-spacing="4">{safe_source}</text>
      <text x="72" y="214" fill="#f6fbff" font-size="56" font-family="Arial, sans-serif" font-weight="700">{safe_title}</text>
      <text x="72" y="610" fill="#a7c8d7" font-size="28" font-family="Arial, sans-serif">Daily AI Frontier</text>
    </svg>
    """.strip()
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def fetch_articles() -> list[dict[str, Any]]:
    timeout = getenv_int("REQUEST_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)
    per_source = getenv_int("ITEMS_PER_SOURCE", DEFAULT_ITEMS_PER_SOURCE)
    headers = {"User-Agent": "ai-frontier-daily/1.0 (+https://github.com/)"}

    collected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for source in SOURCES:
        try:
            response = requests.get(source["feed_url"], headers=headers, timeout=timeout)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Failed to fetch {source['name']}: {exc}", file=sys.stderr)
            continue

        added = 0
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            url = (entry.get("link") or entry.get("id") or "").strip()
            if not title or not url or url.lower() in seen_urls:
                continue

            seen_urls.add(url.lower())
            published_iso = iso_from_struct_time(
                entry.get("published_parsed") or entry.get("updated_parsed")
            )
            summary = strip_html(entry.get("summary") or entry.get("description") or "")
            image_url = extract_image(entry)

            collected.append(
                {
                    "source": source["name"],
                    "kind": source["kind"],
                    "title": title,
                    "url": url,
                    "published_iso": published_iso,
                    "published_display": short_date(published_iso),
                    "summary": summary[:1600],
                    "image_url": image_url,
                }
            )
            added += 1
            if added >= per_source:
                break

    collected.sort(key=lambda item: item["published_iso"], reverse=True)
    return collected[: getenv_int("TOTAL_ITEMS", DEFAULT_TOTAL_ITEMS)]


def summarize_articles(client: genai.Client, model: str, articles: list[dict[str, Any]]) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "page_title": {"type": "string"},
            "page_subtitle": {"type": "string"},
            "overview": {"type": "string"},
            "featured_id": {"type": "integer"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "headline_zh": {"type": "string"},
                        "summary_zh": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "category": {"type": "string"},
                    },
                    "required": [
                        "id",
                        "headline_zh",
                        "summary_zh",
                        "why_it_matters",
                        "category",
                    ],
                },
                "minItems": 10,
            },
        },
        "required": ["page_title", "page_subtitle", "overview", "featured_id", "items"],
    }

    prompt_payload = []
    for idx, article in enumerate(articles, start=1):
        prompt_payload.append(
            {
                "id": idx,
                "source": article["source"],
                "kind": article["kind"],
                "title": article["title"],
                "published": article["published_display"],
                "url": article["url"],
                "summary": article["summary"],
            }
        )

    system_instruction = (
        "You are an elite AI industry editor building a premium-looking daily briefing page in Chinese. "
        "Use only the supplied articles. No fabrication. Keep the tone sharp, modern, and high-signal. "
        "For each item, write concise Chinese copy that explains what happened and why it matters. "
        "Categories should be short labels like Agent, Model, Research, Robotics, Infra, Product, Policy, Open Source."
    )
    user_prompt = (
        "Create a world-class daily AI frontier digest page.\n"
        "Requirements:\n"
        "1. Produce one premium page title, one short subtitle, and one overall overview in Chinese.\n"
        "2. Choose one featured article id that feels the most important today.\n"
        "3. For each item, return a Chinese headline, Chinese summary, a one-line why-it-matters note, and a short category label.\n"
        "4. Keep all output factual and based only on the supplied content.\n\n"
        f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}"
    )

    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.25,
            response_mime_type="application/json",
            response_json_schema=schema,
        ),
    )
    if not response.text:
        raise RuntimeError("Gemini returned an empty response.")
    return json.loads(response.text)


def merge_digest(source_articles: list[dict[str, Any]], digest: dict[str, Any]) -> dict[str, Any]:
    article_by_id = {idx: article for idx, article in enumerate(source_articles, start=1)}
    merged_items = []

    for item in digest["items"]:
        source_item = article_by_id.get(item["id"])
        if not source_item:
            continue
        image_url = source_item["image_url"] or make_placeholder_image(
            source_item["source"], source_item["title"]
        )
        merged_items.append(
            {
                "id": item["id"],
                "source": source_item["source"],
                "kind": source_item["kind"],
                "title": source_item["title"],
                "headline_zh": item["headline_zh"],
                "summary_zh": item["summary_zh"],
                "why_it_matters": item["why_it_matters"],
                "category": item["category"],
                "url": source_item["url"],
                "image_url": image_url,
                "published_display": source_item["published_display"],
            }
        )

    featured_id = digest["featured_id"]
    featured = next((item for item in merged_items if item["id"] == featured_id), None)
    if not featured and merged_items:
        featured = merged_items[0]

    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "page_title": digest["page_title"],
        "page_subtitle": digest["page_subtitle"],
        "overview": digest["overview"],
        "featured": featured,
        "items": merged_items,
        "source_count": len({item["source"] for item in merged_items}),
        "item_count": len(merged_items),
    }


def render_card(item: dict[str, Any], featured: bool = False) -> str:
    card_class = "card featured-card" if featured else "card"
    image = escape(item["image_url"])
    title = escape(item["title"])
    headline_zh = escape(item["headline_zh"])
    summary_zh = escape(item["summary_zh"])
    why_it_matters = escape(item["why_it_matters"])
    source = escape(item["source"])
    category = escape(item["category"])
    date_label = escape(item["published_display"])
    url = escape(item["url"])

    return f"""
    <article class="{card_class}">
      <div class="image-wrap">
        <img src="{image}" alt="{title}" loading="lazy" />
        <div class="image-overlay"></div>
        <span class="category-chip">{category}</span>
      </div>
      <div class="card-body">
        <div class="meta-row">
          <span>{source}</span>
          <span>{date_label}</span>
        </div>
        <h3>{headline_zh}</h3>
        <p class="original-title">{title}</p>
        <p class="summary">{summary_zh}</p>
        <div class="why-box">
          <span>Why it matters</span>
          <p>{why_it_matters}</p>
        </div>
        <a class="read-link" href="{url}" target="_blank" rel="noreferrer">Read source</a>
      </div>
    </article>
    """.strip()


def render_html(digest: dict[str, Any]) -> str:
    featured_html = render_card(digest["featured"], featured=True) if digest["featured"] else ""
    cards_html = "\n".join(
        render_card(item) for item in digest["items"] if not digest["featured"] or item["id"] != digest["featured"]["id"]
    )
    generated_at = escape(digest["generated_at"])
    page_title = escape(digest["page_title"])
    page_subtitle = escape(digest["page_subtitle"])
    overview = escape(digest["overview"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{page_title}</title>
    <meta name="description" content="{page_subtitle}" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Noto+Sans+SC:wght@400;500;700;900&family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet" />
    <style>
      :root {{
        --bg: #07111f;
        --bg-soft: #0b1a2d;
        --panel: rgba(10, 24, 43, 0.72);
        --panel-strong: rgba(8, 19, 34, 0.9);
        --line: rgba(152, 205, 255, 0.14);
        --text: #f5fbff;
        --muted: #9db3c8;
        --cyan: #7de2ff;
        --cyan-strong: #17c6ff;
        --gold: #ffc66d;
        --shadow: 0 30px 80px rgba(0, 0, 0, 0.35);
      }}

      * {{
        box-sizing: border-box;
      }}

      body {{
        margin: 0;
        color: var(--text);
        background:
          radial-gradient(circle at 15% 20%, rgba(23, 198, 255, 0.18), transparent 28%),
          radial-gradient(circle at 85% 10%, rgba(255, 198, 109, 0.14), transparent 22%),
          radial-gradient(circle at 50% 100%, rgba(24, 111, 196, 0.22), transparent 28%),
          linear-gradient(180deg, #07111f 0%, #081321 48%, #050b14 100%);
        font-family: "Noto Sans SC", sans-serif;
      }}

      .frame {{
        width: min(1240px, calc(100% - 32px));
        margin: 0 auto;
        padding: 28px 0 56px;
      }}

      .nav {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 16px;
        padding: 14px 18px;
        border: 1px solid var(--line);
        border-radius: 20px;
        background: rgba(8, 19, 34, 0.65);
        backdrop-filter: blur(14px);
        box-shadow: var(--shadow);
      }}

      .brand {{
        display: flex;
        align-items: center;
        gap: 12px;
        font-family: "Space Grotesk", sans-serif;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        font-size: 13px;
      }}

      .brand-badge {{
        width: 12px;
        height: 12px;
        border-radius: 999px;
        background: linear-gradient(135deg, var(--gold), var(--cyan-strong));
        box-shadow: 0 0 20px rgba(125, 226, 255, 0.7);
      }}

      .nav-note {{
        color: var(--muted);
        font-size: 13px;
      }}

      .hero {{
        margin-top: 28px;
        display: grid;
        grid-template-columns: 1.2fr 0.8fr;
        gap: 22px;
      }}

      .hero-main, .hero-side {{
        border: 1px solid var(--line);
        border-radius: 32px;
        background: var(--panel);
        backdrop-filter: blur(16px);
        box-shadow: var(--shadow);
      }}

      .hero-main {{
        padding: 34px;
        position: relative;
        overflow: hidden;
      }}

      .hero-main::after {{
        content: "";
        position: absolute;
        inset: auto -20% -28% auto;
        width: 340px;
        height: 340px;
        background: radial-gradient(circle, rgba(23, 198, 255, 0.35), transparent 62%);
        pointer-events: none;
      }}

      .eyebrow {{
        font-family: "Space Grotesk", sans-serif;
        font-size: 13px;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--cyan);
      }}

      h1 {{
        margin: 14px 0 10px;
        font-size: clamp(40px, 6vw, 74px);
        line-height: 0.95;
        font-family: "Instrument Serif", serif;
        font-weight: 400;
      }}

      .hero-subtitle {{
        margin: 0;
        max-width: 720px;
        color: var(--text);
        font-size: clamp(18px, 2.3vw, 24px);
        line-height: 1.6;
      }}

      .hero-overview {{
        margin-top: 18px;
        max-width: 760px;
        color: var(--muted);
        font-size: 15px;
        line-height: 1.9;
      }}

      .stat-grid {{
        margin-top: 28px;
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
      }}

      .stat {{
        padding: 16px;
        border-radius: 18px;
        border: 1px solid rgba(125, 226, 255, 0.12);
        background: rgba(255, 255, 255, 0.03);
      }}

      .stat-label {{
        display: block;
        color: var(--muted);
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
      }}

      .stat strong {{
        display: block;
        margin-top: 8px;
        font-family: "Space Grotesk", sans-serif;
        font-size: 26px;
        font-weight: 700;
      }}

      .hero-side {{
        padding: 24px;
      }}

      .side-title {{
        margin: 0 0 14px;
        font-size: 14px;
        font-family: "Space Grotesk", sans-serif;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.12em;
      }}

      .featured-card {{
        min-height: 100%;
      }}

      .section-title {{
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: 16px;
        margin: 34px 0 18px;
      }}

      .section-title h2 {{
        margin: 0;
        font-family: "Space Grotesk", sans-serif;
        font-size: 18px;
        text-transform: uppercase;
        letter-spacing: 0.14em;
      }}

      .section-title span {{
        color: var(--muted);
        font-size: 14px;
      }}

      .grid {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 20px;
      }}

      .card {{
        border: 1px solid var(--line);
        border-radius: 28px;
        overflow: hidden;
        background: var(--panel-strong);
        box-shadow: var(--shadow);
      }}

      .image-wrap {{
        position: relative;
        aspect-ratio: 16 / 9;
        overflow: hidden;
        background: linear-gradient(135deg, #0d233e, #11465a);
      }}

      .image-wrap img {{
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
        transform: scale(1.02);
      }}

      .image-overlay {{
        position: absolute;
        inset: 0;
        background: linear-gradient(180deg, rgba(4, 12, 22, 0.08) 0%, rgba(4, 12, 22, 0.66) 100%);
      }}

      .category-chip {{
        position: absolute;
        left: 16px;
        bottom: 16px;
        z-index: 1;
        padding: 8px 12px;
        border-radius: 999px;
        font-family: "Space Grotesk", sans-serif;
        font-size: 12px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        background: rgba(7, 17, 31, 0.72);
        border: 1px solid rgba(255, 255, 255, 0.12);
      }}

      .card-body {{
        padding: 20px;
      }}

      .meta-row {{
        display: flex;
        justify-content: space-between;
        gap: 12px;
        font-size: 12px;
        color: var(--muted);
        font-family: "Space Grotesk", sans-serif;
        letter-spacing: 0.04em;
        text-transform: uppercase;
      }}

      .card h3 {{
        margin: 14px 0 10px;
        font-size: 24px;
        line-height: 1.3;
      }}

      .original-title {{
        margin: 0 0 14px;
        color: #d2e8f3;
        font-size: 13px;
        line-height: 1.6;
      }}

      .summary {{
        margin: 0;
        color: var(--muted);
        line-height: 1.85;
        font-size: 14px;
      }}

      .why-box {{
        margin-top: 18px;
        padding: 16px;
        border-radius: 18px;
        border: 1px solid rgba(125, 226, 255, 0.12);
        background: rgba(255, 255, 255, 0.03);
      }}

      .why-box span {{
        display: inline-block;
        margin-bottom: 8px;
        color: var(--cyan);
        font-size: 12px;
        font-family: "Space Grotesk", sans-serif;
        letter-spacing: 0.12em;
        text-transform: uppercase;
      }}

      .why-box p {{
        margin: 0;
        font-size: 14px;
        line-height: 1.7;
      }}

      .read-link {{
        display: inline-flex;
        margin-top: 18px;
        color: var(--gold);
        text-decoration: none;
        font-family: "Space Grotesk", sans-serif;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        font-size: 13px;
      }}

      .footer {{
        margin-top: 32px;
        color: var(--muted);
        font-size: 13px;
        text-align: center;
      }}

      @media (max-width: 1080px) {{
        .hero {{
          grid-template-columns: 1fr;
        }}

        .grid {{
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }}
      }}

      @media (max-width: 720px) {{
        .frame {{
          width: min(100% - 20px, 1240px);
          padding-top: 18px;
        }}

        .nav {{
          flex-direction: column;
          align-items: flex-start;
        }}

        .hero-main,
        .hero-side {{
          border-radius: 24px;
        }}

        .hero-main {{
          padding: 22px;
        }}

        .stat-grid,
        .grid {{
          grid-template-columns: 1fr;
        }}

        .card h3 {{
          font-size: 22px;
        }}
      }}
    </style>
  </head>
  <body>
    <div class="frame">
      <nav class="nav">
        <div class="brand">
          <span class="brand-badge"></span>
          <span>Daily AI Frontier</span>
        </div>
        <div class="nav-note">自动更新时间: {generated_at}</div>
      </nav>

      <section class="hero">
        <div class="hero-main">
          <div class="eyebrow">Global AI Briefing</div>
          <h1>{page_title}</h1>
          <p class="hero-subtitle">{page_subtitle}</p>
          <p class="hero-overview">{overview}</p>

          <div class="stat-grid">
            <div class="stat">
              <span class="stat-label">Daily Picks</span>
              <strong>{digest["item_count"]}</strong>
            </div>
            <div class="stat">
              <span class="stat-label">Sources</span>
              <strong>{digest["source_count"]}</strong>
            </div>
            <div class="stat">
              <span class="stat-label">Engine</span>
              <strong>Gemini</strong>
            </div>
          </div>
        </div>

        <aside class="hero-side">
          <p class="side-title">Featured Signal</p>
          {featured_html}
        </aside>
      </section>

      <section>
        <div class="section-title">
          <h2>20 Frontier Signals</h2>
          <span>新闻、模型、产品、研究、开源进展</span>
        </div>
        <div class="grid">
          {cards_html}
        </div>
      </section>

      <div class="footer">
        数据来自公开 RSS 源，页面由 GitHub Actions 每天自动更新。
      </div>
    </div>
  </body>
</html>
"""


def write_outputs(digest: dict[str, Any]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    INDEX_FILE.write_text(render_html(digest), encoding="utf-8")
    NOJEKYLL_FILE.write_text("", encoding="utf-8")


def main() -> int:
    api_key = require_env("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)

    articles = fetch_articles()
    if not articles:
        raise RuntimeError("No articles were fetched from the configured sources.")

    client = genai.Client(api_key=api_key)
    digest_text = summarize_articles(client, model, articles)
    digest = merge_digest(articles, digest_text)
    write_outputs(digest)

    print(f"Generated {digest['item_count']} items into {INDEX_FILE}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)

