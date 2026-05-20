#!/usr/bin/env python3
"""
TechCanto 新聞池轉換器
將 pool/*.md 檔案轉換為 pool-v2/*.json 格式
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime

POOL_DIR = os.path.expanduser("~/.hermes/geniusvps.github.io/pool")
POOL_V2_DIR = os.path.expanduser("~/.hermes/geniusvps.github.io/pool-v2")
USED_NEWS_FILE = os.path.expanduser("~/.hermes/techcanto/config/used_news.json")


def load_used_news():
    """載入已使用新聞記錄"""
    if os.path.exists(USED_NEWS_FILE):
        try:
            with open(USED_NEWS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def parse_md_file(md_path):
    """解析 .md 檔案，提取新聞項目"""
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 提取日期
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', content)
    if not date_match:
        return None, []
    
    date_str = date_match.group(1)
    news_items = []
    
    # 分割新聞項目
    sections = re.split(r'###\s+\d+\.\s+', content)[1:]  # Skip header
    
    for section in sections:
        # 提取標題
        title_match = re.match(r'(.+?)(?:\n|$)', section.strip())
        if not title_match:
            continue
        
        headline = title_match.group(1).strip()
        
        # 提取來源
        source_match = re.search(r'-\s+\*\*來源：\*\*\s+\[([^\]]+)\]\(([^)]+)\)', section)
        source = source_match.group(1) if source_match else "Unknown"
        source_url = source_match.group(2) if source_match else ""
        
        # 提取時間
        time_match = re.search(r'-\s+\*\*時間：\*\*\s+(\d{2}:\d{2})', section)
        time_hkt = time_match.group(1) if time_match else "00:00"
        
        # 提取分類
        cat_match = re.search(r'-\s+\*\*分類：\*\*\s+(\w+)', section)
        category = cat_match.group(1) if cat_match else "general"
        
        # 提取摘要
        summary_match = re.search(r'-\s+\*\*摘要：\*\*\s+(.+?)(?:\n\n|\n-|\n---|\Z)', section, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else headline
        
        # 生成 ID
        seq = len(news_items) + 1
        news_id = f"news_{date_str.replace('-', '')}_{seq:03d}"
        
        news_items.append({
            "id": news_id,
            "date": date_str,
            "headline_zh": headline,
            "headline_en": "",
            "summary_zh": summary[:200],
            "category": category,
            "score": 5,  # 預設分數
            "source": source,
            "source_url": source_url,
            "time_hkt": time_hkt,
            "used_in_episodes": [],
            "verified": True
        })
    
    return date_str, news_items


def convert_all():
    """轉換所有 .md 檔案"""
    os.makedirs(POOL_V2_DIR, exist_ok=True)
    
    used_news = load_used_news()
    converted = 0
    total_news = 0
    
    for md_file in sorted(Path(POOL_DIR).glob("*.md")):
        date_str, news_items = parse_md_file(md_file)
        
        if not news_items:
            continue
        
        # 合併已使用記錄
        for item in news_items:
            if item["id"] in used_news:
                item["used_in_episodes"] = used_news[item["id"]]
        
        # 寫入 JSON
        json_path = os.path.join(POOL_V2_DIR, f"{date_str}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(news_items, f, ensure_ascii=False, indent=2)
        
        converted += 1
        total_news += len(news_items)
        print(f"✅ {date_str}: {len(news_items)} 條新聞")
    
    print(f"\n📊 總計: {converted} 個日期, {total_news} 條新聞")
    return total_news


if __name__ == "__main__":
    print("🔄 TechCanto 新聞池轉換器")
    print("=" * 40)
    count = convert_all()
    print(f"\n✅ 完成！轉換咗 {count} 條新聞")
