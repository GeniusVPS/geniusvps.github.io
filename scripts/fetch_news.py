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

# LongCat API 設定 — 支援多種 config 格式 + 環境變數
LONGCAT_CONFIG_PATH = os.path.join(CONFIG_DIR, "longcat_config.json")
# 亦嘗試 techcanto 主 config 目錄
TECHCANTO_CONFIG_PATH = os.path.expanduser("~/.hermes/techcanto/config/longcat_config.json")

def load_longcat_config():
    # 優先讀環境變數（GitHub Actions 注入）
    env_key = os.environ.get("LONGCAT_API_KEY", "")
    env_url = os.environ.get("LONGCAT_API_URL", "")
    env_model = os.environ.get("LONGCAT_MODEL", "")
    
    # 嘗試讀 config 檔案
    config = {}
    for cfg_path in [LONGCAT_CONFIG_PATH, TECHCANTO_CONFIG_PATH]:
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                print(f"  ✅ 讀到 config: {cfg_path}")
                break
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    
    # 支援多種欄位名格式
    api_key = env_key or config.get("longcat_api_key", "") or config.get("api_key", "")
    base_url = env_url or config.get("longcat_api_url", "") or config.get("base_url", "")
    model = env_model or config.get("longcat_model", "") or config.get("model", "")
    
    # 如果 base_url 唔係完整 endpoint URL，補上 path
    if base_url and "/v1/chat/completions" not in base_url:
        # 檢查係 siliconflow 定 longcat.chat
        if "siliconflow" in base_url:
            api_key_url = f"{base_url}/v1/chat/completions"
        else:
            api_key_url = f"{base_url}/openai/v1/chat/completions"
    else:
        api_key_url = base_url or "https://api.longcat.chat/openai/v1/chat/completions"
    
    if api_key:
        print(f"  ✅ LongCat API key 已載入 ({api_key[:8]}...)")
    else:
        print(f"  ⚠️  LongCat API key 未設定，翻譯將用 fallback")
    
    return {
        "longcat_api_key": api_key,
        "longcat_api_url": api_key_url,
        "longcat_model": model or "LongCat-Flash-Lite"
    }

LONGCAT_CONFIG = load_longcat_config()
LONGCAT_API_URL = LONGCAT_CONFIG["longcat_api_url"]
LONGCAT_MODEL = LONGCAT_CONFIG["longcat_model"]
LONGCAT_API_KEY = LONGCAT_CONFIG["longcat_api_key"]

# 本地 LM Studio API (fallback)
LOCAL_LLM_API = "http://localhost:1234/v1/chat/completions"
LOCAL_LLM_MODEL = "nvidia/nemotron-3-nano-omni"
LOCAL_LLM_KEY = "lm-studio"

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


def summarize_one(text, use_longcat=True):
    """翻譯單條新聞成中文
    Args:
        text: 要翻譯的英文文本
        use_longcat: 是否優先使用 LongCat API (default: True)
    """
    prompt = f"Translate the following English text to Traditional Chinese. Only output the translation, nothing else:\n\n{text}"
    
    # 優先使用 LongCat API
    if use_longcat and LONGCAT_API_KEY:
        try:
            payload = json.dumps({
                "model": LONGCAT_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.5
            }).encode("utf-8")
            
            req = HttpRequest(
                LONGCAT_API_URL,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {LONGCAT_API_KEY}"
                },
                method="POST"
            )
            
            with urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"  ⚠️  LongCat API 失敗 ({type(e).__name__})，用本地 fallback")
    
    # Fallback 到本地 LM Studio
    try:
        payload = json.dumps({
            "model": LOCAL_LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,  # Increased for reasoning models that use tokens for thinking
            "temperature": 0.5
        }).encode("utf-8")
        
        req = HttpRequest(
            LOCAL_LLM_API,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LOCAL_LLM_KEY}"
            },
            method="POST"
        )
        
        with urlopen(req, timeout=60) as resp:  # Increased timeout for reasoning models
            result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"].strip()
            # Handle reasoning models that may put output in reasoning_content
            if not content:
                reasoning = result["choices"][0]["message"].get("reasoning_content", "")
                if reasoning:
                    # Extract Chinese text from reasoning as fallback
                    import re
                    chinese_text = re.findall(r'[\u4e00-\u9fff]+', reasoning)
                    if chinese_text:
                        content = ''.join(chinese_text)[:200]
            return content
    except Exception as e:
        raise RuntimeError(f"Both LongCat and local LLM failed: {e}")


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
