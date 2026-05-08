#!/usr/bin/env python3
"""
TechCanto News Pool — RSS 新聞抓取 + 去重 + 粵語摘要
Optimized v2: 批量摘要 + 超時保護
"""

import json
import os
import sys
import hashlib
import re
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from xml.etree import ElementTree as ET
from urllib.request import urlopen, Request
from urllib.error import URLError
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

# ─── 設定 ───
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
POOL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pool")
LONGCAT_API = "https://api.longcat.chat/openai"
LONGCAT_MODEL = "LongCat-Flash-Chat"
LONGCAT_KEY = os.environ.get("LONGCAT_API_KEY", "")

SIMILARITY_THRESHOLD = 0.85
MAX_PER_FEED = 5       # 每來源最多 5 條
MAX_TOTAL = 20        # 總數上限
RSS_TIMEOUT = 10      # 每個 RSS 超時秒數
SUMMARY_BATCH_SIZE = 5  # 每批摘要數量

HKT = timezone(timedelta(hours=8))


# ─── 工具函數 ───
def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def content_hash(text):
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def similarity(a, b):
    a = re.sub(r"[^\w\s\u4e00-\u9fff]", "", a.lower())
    b = re.sub(r"[^\w\s\u4e00-\u9fff]", "", b.lower())
    return SequenceMatcher(None, a, b).ratio()


def is_duplicate(title, url, desc_hash, seen_urls, seen_hashes, seen_titles):
    if url in seen_urls:
        return True, "url_match"
    if desc_hash in seen_hashes:
        return True, "hash_match"
    for st in seen_titles:
        if similarity(title, st) >= SIMILARITY_THRESHOLD:
            return True, "title_similar"
    return False, None


def fetch_rss(url, timeout=RSS_TIMEOUT):
    try:
        req = Request(url, headers={"User-Agent": "TechCanto-NewsPool/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        print(f"  ⚠️  RSS failed ({timeout}s timeout): {url[:50]}... -> {type(e).__name__}")
        return None


def parse_rss(data):
    items = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return items

    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        desc = item.findtext("description", "").strip()
        pubdate = item.findtext("pubDate", "").strip()
        if title:
            items.append({"title": title, "link": link, "description": desc, "pubDate": pubdate})

    if not items:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            title = entry.findtext("atom:title", namespaces=ns, default="").strip()
            link_el = entry.find("atom:link", namespaces=ns)
            link = link_el.get("href", "") if link_el is not None else ""
            desc = entry.findtext("atom:summary", namespaces=ns, default="").strip()
            pubdate = entry.findtext("atom:published", namespaces=ns, default="").strip()
            if title:
                items.append({"title": title, "link": link, "description": desc, "pubDate": pubdate})

    return items


def clean_html(text):
    return re.sub(r"<[^>]+>", "", text).strip()


def summarize_batch(items):
    """批量生成摘要 — 一次 API call 處理多條新聞"""
    if not items:
        return {}

    batch_lines = []
    for idx, item in enumerate(items):
        batch_lines.append(
            f"新聞 {idx+1}:\n標題: {item['title']}\n來源: {item['source']}\n描述: {clean_html(item.get('description', ''))[:300]}"
        )
    batch_text = "\n\n".join(batch_lines)

    prompt = f"""你係一個廣東話科技新聞專家。以下有 {len(items)} 條科技新聞，請為每條新聞寫一段簡短嘅廣東話摘要（每條 50-80 字）。

要求：
- 用口語化廣東話（繁體中文）
- 重點清晰
- 每條摘要用「摘要1:」、「摘要2:」等做開頭

{batch_text}

請依次回覆每條新聞嘅摘要："""

    try:
        import urllib.request
        payload = json.dumps({
            "model": LONGCAT_MODEL,
            "messages": [
                {"role": "system", "content": "你係廣東話科技新聞摘要專家，用口語化廣東話寫簡短摘要。"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 1500,
            "temperature": 0.7
        }).encode("utf-8")

        req = urllib.request.Request(
            LONGCAT_API + "/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LONGCAT_KEY}"
            }
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        full_summary = result["choices"][0]["message"]["content"].strip()

        # 解析每條摘要 — 用分隔符號切分
        summaries = {}
        # 找所有 "摘要N:" 嘅位置
        parts = re.split(r"摘要\d+[:：]\s*", full_summary)
        parts = [p.strip() for p in parts if p.strip()]

        for i, item in enumerate(items):
            if i < len(parts):
                summaries[item["link"]] = parts[i][:150]
            else:
                summaries[item["link"]] = clean_html(item.get("description", ""))[:150] or "(無摘要)"

        return summaries
    except Exception as e:
        print(f"  ⚠️  Batch summary API failed: {e}")
        return {item["link"]: "(摘要失敗)" for item in items}


# ─── 主邏輯 ───
def main():
    print("🔍 TechCanto News Pool — 開始抓取新聞")
    print("=" * 50)

    sources_path = os.path.join(CONFIG_DIR, "sources.json")
    with open(sources_path, "r", encoding="utf-8") as f:
        sources = json.load(f)["feeds"]

    seen_urls = load_json(os.path.join(CONFIG_DIR, "seen_urls.json"))
    seen_hashes = load_json(os.path.join(CONFIG_DIR, "seen_hashes.json"))
    seen_titles = load_json(os.path.join(CONFIG_DIR, "seen_titles.json"))

    print(f"📡 來源: {len(sources)} | 📋 已記錄: {len(seen_urls)} URLs")
    print()

    now = datetime.now(HKT)
    date_str = now.strftime("%Y-%m-%d")
    candidates = []
    new_items = []
    skipped = 0
    errors = 0

    # Phase 1: 抓取所有 RSS
    for source in sources:
        name = source["name"]
        url = source["url"]
        print(f"📰 {name}...", end=" ", flush=True)

        data = fetch_rss(url)
        if not data:
            errors += 1
            print("❌")
            continue

        items = parse_rss(data)[:MAX_PER_FEED]
        feed_new = 0

        for item in items:
            if len(candidates) >= MAX_TOTAL:
                break

            title = item["title"]
            link = item["link"]
            desc = clean_html(item["description"])
            desc_hash = content_hash(desc) if desc else content_hash(title)

            is_dup, reason = is_duplicate(title, link, desc_hash, seen_urls, seen_hashes, seen_titles)

            if is_dup:
                skipped += 1
                continue

            candidates.append({
                "title": title,
                "link": link,
                "source": name,
                "description": desc,
                "time": now.strftime("%H:%M"),
                "category": source.get("category", "general")
            })
            feed_new += 1

        print(f"✅ +{feed_new}")

    new_items = []

    # Phase 2: 批量生成摘要
    if candidates:
        print(f"\n🤖 生成 {len(candidates)} 條新聞嘅粵語摘要...")

        for i in range(0, len(candidates), SUMMARY_BATCH_SIZE):
            batch = candidates[i:i + SUMMARY_BATCH_SIZE]
            summaries = summarize_batch(batch)

            for item in batch:
                item["summary"] = summaries.get(item["link"], "(無摘要)")
                new_items.append(item)

                seen_urls.append(item["link"]) if item["link"] else None
                seen_hashes.append(content_hash(item["description"]))
                seen_titles.append(item["title"])

        # 儲存去重記錄
        save_json(os.path.join(CONFIG_DIR, "seen_urls.json"), seen_urls)
        save_json(os.path.join(CONFIG_DIR, "seen_hashes.json"), seen_hashes)
        save_json(os.path.join(CONFIG_DIR, "seen_titles.json"), seen_titles)

        # 寫入每日新聞檔案
        pool_file = os.path.join(POOL_DIR, f"{date_str}.md")
        exists = os.path.exists(pool_file)

        with open(pool_file, "a", encoding="utf-8") as f:
            if not exists:
                f.write(f"# 📰 TechCanto 新聞池 — {date_str}\n\n")
                f.write(f"> 自動生成於 {now.strftime('%Y-%m-%d %H:%M HKT')}\n\n")
                f.write(f"**今日新增：** {len(new_items)} 條\n\n")
                f.write("---\n\n")
            else:
                f.write(f"## 🔄 更新於 {now.strftime('%H:%M HKT')}\n\n")

            for idx, item in enumerate(new_items, 1):
                f.write(f"### {idx}. {item['title']}\n\n")
                f.write(f"- **來源：** [{item['source']}]({item['link']})\n")
                f.write(f"- **時間：** {item['time']} HKT\n")
                f.write(f"- **分類：** {item['category']}\n")
                f.write(f"- **摘要：** {item['summary']}\n\n")
                f.write("---\n\n")

        print(f"📝 已寫入: {pool_file}")
    else:
        print("\n📭 今日無新新聞")

    print(f"\n{'=' * 50}")
    print(f"✅ 完成！新增: {len(new_items)} | 跳過: {skipped} | 錯誤: {errors}")
    return len(new_items)


if __name__ == "__main__":
    count = main()
    sys.exit(0 if count >= 0 else 1)
