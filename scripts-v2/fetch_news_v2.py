# TechCanto News Pool v2.0 (Testing/Optimization)
# This is a parallel version for testing new features without affecting production (v1).
# DO NOT run this in production until validated.
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
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config-v2")
POOL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pool-v2")

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


# ─── 智能分類 + 粵語口語翻譯 + 新聞評分 ───
CATEGORIES_DESC = {
    "ai": "人工智能、機器學習、LLM、大語言模型、AI 工具",
    "hardware": "硬件、芯片、手機、平板、穿戴設備、GPU",
    "software": "軟件、應用程式、操作系統、開發工具",
    "network": "網絡、互聯網、5G、寬帶、Wi-Fi",
    "security": "網絡安全、黑客、漏洞、數據洩漏、隱私",
    "business": "科技商業、裁員、收購、IPO、融資、股價",
    "gaming": "遊戲、PlayStation、Xbox、Nintendo、Steam",
    "cloud": "雲端、AWS、Azure、GCP、數據中心",
    "space": "太空、NASA、SpaceX、火箭、衛星",
    "general": "其他科技新聞",
}

CATEGORIES_JSON = json.dumps(CATEGORIES_DESC, ensure_ascii=False, indent=2)

# 評分閾值 — 低於呢個分數嘅新聞會被過濾
SCORE_THRESHOLD = 4  # 1-10 分，低於 4 分嘅新聞唔會入池

SCORING_CRITERIA = """
評分標準（1-10 分）：
- 新聞價值：呢條新聞有無重要意義？影響幾多人？
- 受眾相關性：香港/粵語受眾會唔會感兴趣？
- 獨特性：係咪獨家或罕見消息？定係常見重複內容？
- 時效性：係咪最新嘅消息？定係舊聞？

分數指引：
- 9-10: 極高價值，重大突破性新聞
- 7-8: 高價值，重要行業動態
- 5-6: 中等價值，一般科技新聞
- 3-4: 低價值，邊緣或重複內容
- 1-2: 無價值，廣告、促銷、低質內容
"""

def build_translate_prompt(title, description=""):
    """建構智能分類 + 粵語口語翻譯 + 詳細摘要 + 評分嘅 prompt"""
    return f"""你係一個粵語科技新聞編輯。請做四件事：
1. 將以下英文新聞標題翻譯成粵語口語風格嘅中文標題
2. 根據新聞標題同描述，寫一段詳細嘅粵語口語摘要（100-150 字），包含新聞背景、重點同影響
3. 從以下分類中選擇最合適的一個分類
4. 根據評分標準給予 1-10 分嘅新聞評分

可用分類：
{CATEGORIES_JSON}

{SCORING_CRITERIA}

新聞標題：{title}
{"新聞描述：" + description[:500] if description else ""}

請以 JSON 格式回复，只回复 JSON，不要其他文字：
{{"headline_zh": "粵語口語標題", "summary_zh": "詳細粵語摘要 100-150 字", "category": "分類代碼", "score": 分數}}"""


def call_llm_api(prompt, api_url, api_key, model, max_tokens, timeout):
    """通用 LLM API 調用函數"""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.5
    }).encode("utf-8")
    
    req = HttpRequest(
        api_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )
    
    with urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"].strip()


def summarize_one(title, description="", use_longcat=True):
    """智能分類 + 粵語口語翻譯 + 新聞評分
    返回 dict: {"headline_zh": "粵語標題", "category": "分類代碼", "score": 分數}
    如果失敗返回 None
    """
    prompt = build_translate_prompt(title, description)
    
    # 優先使用 LongCat API
    if use_longcat and LONGCAT_API_KEY:
        try:
            content = call_llm_api(
                prompt, LONGCAT_API_URL, LONGCAT_API_KEY,
                LONGCAT_MODEL, 300, 30
            )
            return parse_llm_response(content)
        except Exception as e:
            print(f"  ⚠️  LongCat API 失敗 ({type(e).__name__})，用本地 fallback")
    
    # Fallback 到本地 LM Studio
    try:
        content = call_llm_api(
            prompt, LOCAL_LLM_API, LOCAL_LLM_KEY,
            LOCAL_LLM_MODEL, 500, 60
        )
        return parse_llm_response(content)
    except Exception as e:
        print(f"  ⚠️  本地 LLM 亦失敗 ({type(e).__name__})")
        return None


def parse_llm_response(content):
    """解析 LLM 返回嘅 JSON 響應"""
    # 嘗試直接解析 JSON
    try:
        data = json.loads(content)
        if "headline_zh" in data and "category" in data:
            # 驗證分類
            valid_cats = list(CATEGORIES_DESC.keys())
            if data["category"] in valid_cats:
                # 驗證評分
                data.setdefault("score", 5)  # 預設中等分數
                try:
                    data["score"] = int(data["score"])
                    data["score"] = max(1, min(10, data["score"]))  # 限制 1-10
                except (ValueError, TypeError):
                    data["score"] = 5
                # 如果冇 summary_zh，用 headline_zh 做 fallback
                if "summary_zh" not in data or not data["summary_zh"]:
                    data["summary_zh"] = data["headline_zh"]
                return data
            else:
                data["category"] = "general"
                data.setdefault("score", 5)
                if "summary_zh" not in data or not data["summary_zh"]:
                    data["summary_zh"] = data.get("headline_zh", "")
                return data
    except (json.JSONDecodeError, KeyError):
        pass
    
    # 嘗試從文本中提取 JSON
    import re
    json_match = re.search(r'\{[^{}]*\}', content)
    if json_match:
        try:
            data = json.loads(json_match.group())
            if "headline_zh" in data:
                data.setdefault("category", "general")
                data.setdefault("score", 5)
                if "summary_zh" not in data or not data["summary_zh"]:
                    data["summary_zh"] = data["headline_zh"]
                return data
        except json.JSONDecodeError:
            pass
    
    # 最終 fallback — 提取中文文字
    chinese_text = re.findall(r'[\u4e00-\u9fff\u3000-\u303f]+', content)
    if chinese_text:
        return {"headline_zh": ''.join(chinese_text)[:150], "summary_zh": ''.join(chinese_text)[:150], "category": "general", "score": 5}
    
    return None


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
    """批量生成粵語摘要 + 智能分類 + 新聞評分
    返回 dict: {link: {"headline_zh": 粵語標題, "summary": 粵語摘要, "category": AI分類, "score": 評分}}
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
        
        try:
            # 調用 LLM 做智能分類 + 粵語翻譯 + 評分
            result = summarize_one(title, desc)
            
            if result is None:
                raise ValueError("LLM returned None")
            
            zh_headline = result.get("headline_zh", "")
            zh_summary = result.get("summary_zh", zh_headline)  # 用真正摘要，冇先用 headline fallback
            ai_category = result.get("category", "general")
            score = result.get("score", 5)
            
            if not zh_headline or len(zh_headline) < 3:
                raise ValueError("LLM returned empty/too short")
            
            # 簡繁轉換
            trad_headline = simp_to_trad(zh_headline)
            trad_summary = simp_to_trad(zh_summary)
            
            summaries[item["link"]] = {
                "headline_zh": trad_headline[:150],
                "summary": trad_summary[:300],  # 用真正摘要，非複製標題
                "category": ai_category,
                "score": score
            }
            translated += 1
            print(f"  ✅ [{i+1}/{len(items)}] [{ai_category}] ⭐{score} {trad_headline[:40]}...")
            
        except Exception as e:
            fallback_count += 1
            print(f"  ⚠️  [{i+1}/{len(items)}] 翻譯失敗，用原文: {title[:40]}...")
            fallback = desc[:200] if desc else title
            summaries[item["link"]] = {
                "headline_zh": title,
                "summary": fallback[:150] or f"{source}: {title}",
                "category": item.get("category", "general"),
                "score": 5  # fallback 預設中等分數
            }

    print(f"  📊 翻譯成功: {translated}   Fallback: {fallback_count}")
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

    print(f"📡 來源: {len(sources)}   📋 已記錄: {len(seen_urls)} URLs")
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
    filtered_low_score = 0

    # Phase 2: 批量生成摘要 + 評分
    if candidates:
        print(f"\n🤖 生成 {len(candidates)} 條新聞嘅粵語摘要 + 評分...")

        for i in range(0, len(candidates), SUMMARY_BATCH_SIZE):
            batch = candidates[i:i + SUMMARY_BATCH_SIZE]
            summaries = summarize_batch(batch)

            for item in batch:
                summary_data = summaries.get(item["link"], {"headline_zh": item["title"], "summary": "(無摘要)", "category": item["category"], "score": 5})
                item["headline_zh"] = summary_data["headline_zh"]
                item["summary"] = summary_data["summary"]
                item["category"] = summary_data.get("category", item["category"])
                item["score"] = summary_data.get("score", 5)
                
                # 評分過濾 — 低於閾值嘅新聞唔會入池
                if item["score"] < SCORE_THRESHOLD:
                    filtered_low_score += 1
                    print(f"  🗑️  低分過濾 (⭐{item['score']}): {item['headline_zh'][:40]}...")
                    continue
                
                new_items.append(item)

                seen_urls.append(item["link"]) if item["link"] else None
                seen_hashes.append(content_hash(item["description"]))
                seen_titles.append(item["title"])

        # 按評分排序（高分優先）
        new_items.sort(key=lambda x: x.get("score", 5), reverse=True)
        
        # 限制總數
        if len(new_items) > MAX_TOTAL:
            print(f"\n✂️  截斷至 {MAX_TOTAL} 條最高分新聞")
            new_items = new_items[:MAX_TOTAL]
        
        if filtered_low_score > 0:
            print(f"\n📊 評分過濾：{filtered_low_score} 條低分新聞被排除")

        # 儲存去重記錄
        save_json(os.path.join(CONFIG_DIR, "seen_urls.json"), seen_urls)
        save_json(os.path.join(CONFIG_DIR, "seen_hashes.json"), seen_hashes)
        save_json(os.path.join(CONFIG_DIR, "seen_titles.json"), seen_titles)

        # 寫入每日新聞檔案（Markdown + JSON 雙格式）
        pool_file = os.path.join(POOL_DIR, f"{date_str}.md")
        pool_json_file = os.path.join(POOL_DIR, f"{date_str}.json")
        exists = os.path.exists(pool_file)

        # 載入已有 JSON（如果存在）
        existing_json = []
        if os.path.exists(pool_json_file):
            try:
                with open(pool_json_file, "r", encoding="utf-8") as jf:
                    existing_json = json.load(jf)
            except (json.JSONDecodeError, FileNotFoundError):
                existing_json = []

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
                score = item.get("score", 5)
                # 評分星號顯示
                stars = "⭐" * score
                f.write(f"### {idx}. {headline}\n\n")
                f.write(f"- **來源：** [{item['source']}]({item['link']})\n")
                f.write(f"- **時間：** {item['time']} HKT\n")
                f.write(f"- **分類：** {item['category']}\n")
                f.write(f"- **評分：** {stars} ({score}/10)\n")
                f.write(f"- **摘要：** {item['summary']}\n\n")
                f.write("---\n\n")

        print(f"📝 已寫入: {pool_file}")

        # 生成唯一新聞 ID 並寫入 JSON
        # 計算已有新聞數量（用嚟決定 sequence number）
        base_sequence = len(existing_json) + 1
        new_json_items = []

        for idx, item in enumerate(new_items, 1):
            news_id = f"news_{date_str.replace('-', '')}_{base_sequence + idx - 1:03d}"
            new_json_items.append({
                "id": news_id,
                "date": date_str,
                "headline_zh": item.get("headline_zh", item["title"]),
                "headline_en": item["title"],
                "summary_zh": item["summary"],
                "category": item.get("category", "general"),
                "score": item.get("score", 5),
                "source": item["source"],
                "source_url": item["link"],
                "time_hkt": item["time"],
                "used_in_episodes": [],  # 追蹤邊集數用過呢條新聞
                "verified": True
            })

        # 合併同已有新聞
        all_news = existing_json + new_json_items

        # 寫入 JSON
        with open(pool_json_file, "w", encoding="utf-8") as jf:
            json.dump(all_news, jf, ensure_ascii=False, indent=2)

        print(f"📦 已寫入 JSON: {pool_json_file} ({len(all_news)} 條新聞)")
    else:
        print("\n📭 今日無新新聞")

    print(f"\n{'=' * 50}")
    print(f"✅ 完成！新增: {len(new_items)}   跳過: {skipped}   錯誤: {errors}")
    return len(new_items)


if __name__ == "__main__":
    count = main()
    sys.exit(0 if count >= 0 else 1)

