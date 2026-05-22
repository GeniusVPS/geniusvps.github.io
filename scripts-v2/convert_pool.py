#!/usr/bin/env python3
"""
TechCanto 新聞池轉換器
將 pool/*.md 檔案轉換為 pool-v2/*.json 格式
保留 used_in_episodes 數據（不受 git pull 影響）
"""

import json
import os
import re
from datetime import datetime


# ─── 路徑設定 ───
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
POOL_DIR = os.path.join(BASE_DIR, "pool")
POOL_V2_DIR = os.path.join(BASE_DIR, "pool-v2")
CONFIG_DIR = os.path.join(BASE_DIR, "config-v2")
USED_NEWS_FILE = os.path.join(CONFIG_DIR, "used_news.json")

# 亦嘗試 techcanto 主 config
TECHCANTO_USED_NEWS = os.path.expanduser("~/.hermes/techcanto/config/used_news.json")


def load_used_news():
    """載入已使用新聞記錄"""
    for path in [USED_NEWS_FILE, TECHCANTO_USED_NEWS]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"  ✅ 讀到 used_news: {path} ({len(data)} entries)")
                return data
            except (json.JSONDecodeError, IOError) as e:
                print(f"  ⚠️  used_news 讀取失敗: {e}")
    print("  ⚠️  used_news.json 唔存在，建立新檔案")
    return {}


def save_used_news(data):
    """儲存已使用新聞記錄"""
    os.makedirs(os.path.dirname(USED_NEWS_FILE), exist_ok=True)
    with open(USED_NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_md_file(md_path):
    """解析 .md 檔案，提取新聞項目"""
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    date_str = os.path.basename(md_path).replace(".md", "")
    items = []

    # 按 ### 分隔新聞項目
    blocks = content.split("### ")
    for block in blocks[1:]:  # 跳過標題之前嘅內容
        lines = block.strip().split("\n")
        if not lines:
            continue

        # 第一行：編號 + 標題
        first_line = lines[0].strip()
        match = re.match(r"(\d+)\.\s+(.*)", first_line)
        if not match:
            continue

        num = int(match.group(1))
        headline = match.group(2).strip()

        # 提取 metadata
        source = ""
        source_url = ""
        time_hkt = ""
        category = "general"
        summary = ""

        for line in lines[1:]:
            line = line.strip()
            if "**來源：**" in line:
                # 提取 [Source](URL)
                url_match = re.search(r"\[([^\]]+)\]\(([^)]+)\)", line)
                if url_match:
                    source = url_match.group(1)
                    source_url = url_match.group(2)
            elif "**時間：**" in line:
                time_hkt = line.split("**時間：**")[1].strip()
            elif "**分類：**" in line:
                category = line.split("**分類：**")[1].strip()
            elif "**摘要：**" in line:
                summary = line.split("**摘要：**")[1].strip()

        # 生成唯一 ID
        news_id = f"{date_str}_{num:03d}"

        items.append({
            "id": news_id,
            "date": date_str,
            "headline_zh": headline,
            "headline_en": headline,  # md 冇英文標題，用中文代替
            "summary_zh": summary,
            "category": category,
            "score": 5,  # md 冇評分，用預設值
            "source": source,
            "source_url": source_url,
            "time_hkt": time_hkt,
            "used_in_episodes": [],
            "verified": True
        })

    return date_str, items


def convert_all():
    """轉換所有 .md 檔案到 JSON"""
    used_news = load_used_news()

    if not os.path.exists(POOL_DIR):
        print(f"❌ Pool directory not found: {POOL_DIR}")
        return 0

    os.makedirs(POOL_V2_DIR, exist_ok=True)

    md_files = sorted([f for f in os.listdir(POOL_DIR) if f.endswith(".md")])
    total_converted = 0

    for md_file in md_files:
        md_path = os.path.join(POOL_DIR, md_file)
        date_str, items = parse_md_file(md_path)

        if not items:
            continue

        # 合併已使用記錄
        for item in items:
            if item["id"] in used_news and used_news[item["id"]]:
                item["used_in_episodes"] = used_news[item["id"]]

        # 寫入 JSON
        json_path = os.path.join(POOL_V2_DIR, f"{date_str}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

        total_converted += len(items)
        print(f"  ✅ {md_file} → {date_str}.json ({len(items)} items)")

    # 同步 used_in_episodes 到 used_news.json
    for item in load_all_pool_v2():
        nid = item.get("id", "")
        episodes = item.get("used_in_episodes", [])
        if nid and episodes:
            used_news[nid] = episodes
    save_used_news(used_news)

    return total_converted


def load_all_pool_v2():
    """載入 pool-v2 所有新聞"""
    all_news = []
    if not os.path.exists(POOL_V2_DIR):
        return all_news

    for filename in os.listdir(POOL_V2_DIR):
        if filename.endswith(".json"):
            filepath = os.path.join(POOL_V2_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    all_news.extend(json.load(f))
            except (json.JSONDecodeError, IOError):
                pass

    return all_news


if __name__ == "__main__":
    print("🔄 TechCanto 新聞池轉換器")
    print("=" * 40)
    count = convert_all()
    print(f"\n✅ 完成！轉換咗 {count} 條新聞")
