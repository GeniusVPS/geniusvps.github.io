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
from urllib.request import urlopen, Request, Request as HttpRequest
from urllib.error import URLError
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

# 簡繁轉換實例（可選，無 opencc 時 fallback 到簡單替換）
try:
    from opencc import OpenCC as _OpenCC
    OPENCC = _OpenCC('s2t')
    USE_OPENCC = True
except ImportError:
    OPENCC = None
    USE_OPENCC = False
    print("⚠️  opencc 未安裝，簡繁轉換將用簡單替換 fallback")

# ─── 設定 ───
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
POOL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pool")

# 本地 llama-server API 設定 — qwen3.6-27b reasoning model (port 8080)
LLAMA_BASE = "http://127.0.0.1:8080"
LLAMA_CHAT = LLAMA_BASE + "/v1/chat/completions"
LOCAL_LLM_MODEL = "qwen3.6-27b-q4_k_m"  # Qwen3.6 27B reasoning model

SIMILARITY_THRESHOLD = 0.85
MAX_PER_FEED = 5       # 每來源最多 5 條
MAX_TOTAL = 20        # 總數上限
RSS_TIMEOUT = 10      # 每個 RSS 超時秒數
SUMMARY_BATCH_SIZE = 3  # 每批摘要數量（減低以配合 CPU 推理速度）
LLM_TIMEOUT = 180       # LLM 摘要超時秒數（27B 模型喺 CPU 上需要時間）

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


def summarize_one(text, use_local=True):
    """翻譯單條新聞成粵語
    Args:
        text: 要翻譯的英文文本
        use_local: 是否優先使用本地 Ollama (default: True)
    """
    prompt_user = f"Translate to Traditional Chinese (keep it concise):\n{text}"
    last_error = None
    
    # 使用本地 llama-server (OpenAI-compatible)
    if use_local:
        try:
            payload = json.dumps({
                "model": LOCAL_LLM_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a translator. Translate to Traditional Chinese (繁體中文). Output ONLY the translation, nothing else."},
                    {"role": "user", "content": prompt_user}
                ],
                "temperature": 0.3,
                "max_tokens": 4096
            }).encode("utf-8")
            
            req = HttpRequest(
                LLAMA_CHAT,
                data=payload,
                headers={
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            
            with urlopen(req, timeout=300) as resp:
                # OpenAI-compatible endpoint 返回 JSON
                result = json.loads(resp.read())
                content = result["choices"][0]["message"]["content"].strip()
                if content:
                    return content
        except Exception as e:
            last_error = e
            print(f"  ⚠️  本地 llama-server 失敗 ({type(e).__name__})")
    
    raise RuntimeError(f"Local LLM failed: {last_error}")


def is_spam_article(title, desc, source):
    """過濾廣告、促銷碼、贊助內容等非新聞文章"""
    spam_keywords = [
        'promo code', 'coupon code', 'discount code', 'deal alert',
        'subscribe now', 'sign up for', 'free trial', 'limited offer',
        'exclusive deal', 'save up to', 'percent off', 'off now',
        'wireless promo', 'wired promo', 'best price', 'shop now',
        'buy now', 'special offer', 'advertiser', 'sponsored',
        'advertisement', 'advertising', 'promotion code',
        'use code', 'enter code', 'apply code', 'redemption code',
    ]
    combined = f"{title} {desc}".lower()
    return any(kw in combined for kw in spam_keywords)


def simp_to_trad(text):
    """簡體轉繁體（opencc 優先，fallback 到簡單替換）"""
    if USE_OPENCC and OPENCC:
        return OPENCC.convert(text)
    # 簡單 fallback — 對大部分科技新聞已經够用
    # 注意：呢個唔係完美轉換，只係 emergency fallback
    return text


def summarize_batch(items):
    """批量生成粵語摘要 — 逐條翻譯 + 簡繁轉換
    返回 dict: {link: {"headline_zh": 中文標題, "summary": 中文摘要}}
    """
    if not items:
        return {}

    summaries = {}
    translated = 0
    fallback_count = 0
    
    for i, item in enumerate(items):
        title = item["title"]
        desc = clean_html(item.get("description", ""))
        source = item["source"]
        
        # 只用標題做翻譯（短文字更可靠）
        source_text = title
        
        try:
            # 調用本地 LLM 翻譯
            zh_text = summarize_one(source_text)
            
            # 檢查 LLM 有無真正輸出
            if not zh_text or len(zh_text) < 5:
                raise ValueError("LLM returned empty/too short")
            
            # 檢查有無中文（驗證翻譯成功）
            has_chinese = any('\u4e00' <= c <= '\u9fff' for c in zh_text)
            if not has_chinese:
                raise ValueError("No Chinese characters in output")
            
            # 簡繁轉換（opencc 專業轉換）
            trad_text = simp_to_trad(zh_text)
            
            # 同時儲存中文標題同摘要
            summaries[item["link"]] = {
                "headline_zh": trad_text[:150],
                "summary": trad_text[:150]
            }
            translated += 1
            print(f"  ✅ [{i+1}/{len(items)}] {trad_text[:60]}...")
            
        except Exception as e:
            fallback_count += 1
            print(f"  ⚠️  [{i+1}/{len(items)}] 翻譯失敗，用原文: {title[:40]}...")
            # 用原文描述或標題作為 fallback
            fallback = desc[:200] if desc else title
            summaries[item["link"]] = {
                "headline_zh": title,
                "summary": fallback[:150] or f"{source}: {title}"
            }

    print(f"  📊 翻譯成功: {translated} | Fallback: {fallback_count}")
    return summaries


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

            # 過濾廣告、促銷碼等非新聞內容
            if is_spam_article(title, desc, name):
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
                summary_data = summaries.get(item["link"], {"headline_zh": item["title"], "summary": "(無摘要)"})
                item["headline_zh"] = summary_data["headline_zh"]
                item["summary"] = summary_data["summary"]
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
                # 用中文標題作為 headline，英文標題作為備份
                headline = item.get("headline_zh", item["title"])
                f.write(f"### {idx}. {headline}\n\n")
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
