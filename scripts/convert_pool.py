#!/usr/bin/env python3
"""
TechCanto 新聞池轉換器
將 pool/*.md 檔案轉換為 pool-v2/*.json 格式
加入 AI 分類功能（使用本地 LM Studio API）
"""

import json
import os
import re
import requests
from pathlib import Path
from datetime import datetime

POOL_DIR = os.path.expanduser("~/.hermes/geniusvps.github.io/pool")
POOL_V2_DIR = os.path.expanduser("~/.hermes/geniusvps.github.io/pool-v2")
USED_NEWS_FILE = os.path.expanduser("~/.hermes/techcanto/config/used_news.json")

# 本地 LLM 設定
LOCAL_LLM_URL = "http://localhost:1234/v1/chat/completions"
LOCAL_LLM_MODEL = "nvidia/nemotron-3-nano-omni"
LOCAL_LLM_KEY = "lm-studio"

# 可用分類
CATEGORIES = ["ai", "hardware", "software", "network", "security", "business", "gaming", "cloud", "space", "general"]


def load_used_news():
    """載入已使用新聞記錄"""
    if os.path.exists(USED_NEWS_FILE):
        try:
            with open(USED_NEWS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def classify_news(headline, summary):
    """使用本地 LLM 分類新聞"""
    try:
        prompt = f"""Classify this news into ONE of these categories: {', '.join(CATEGORIES)}
Return ONLY the category name, nothing else.

Headline: {headline}
Summary: {summary[:200]}

Category:"""
        
        response = requests.post(
            LOCAL_LLM_URL,
            headers={"Authorization": f"Bearer {LOCAL_LLM_KEY}"},
            json={
                "model": LOCAL_LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 10,
                "temperature": 0.1
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()["choices"][0]["message"]["content"].strip().lower()
            # 檢查是否喺有效分類入面
            for cat in CATEGORIES:
                if cat in result:
                    return cat
            return "general"
        else:
            return "general"
    except Exception:
        return "general"


def classify_batch(news_items, batch_size=10):
    """批量分類新聞"""
    print(f"🤖 分類 {len(news_items)} 條新聞...")
    
    for i in range(0, len(news_items), batch_size):
        batch = news_items[i:i + batch_size]
        for item in batch:
            headline = item.get("headline_zh", "")
            summary = item.get("summary_zh", "")
            item["category"] = classify_news(headline, summary)
            print(f"  ✅ [{i+batch.index(item)+1}/{len(news_items)}] {item['category']}: {headline[:40]}...")
    
    return news_items


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
        
        # 提取原始分類（用作 fallback）
        cat_match = re.search(r'-\s+\*\*分類：\*\*\s+(\w+)', section)
        original_category = cat_match.group(1) if cat_match else "general"
        
        # 提取摘要
        summary_match = re.search(r'-\s+\*\*摘要：\*\*\s+(.+?)(?:\n\n|\n-|\n---|\Z)', section, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else headline
        
        # 過濾掉網址行同垃圾數據 (Article URL, Comments URL, Points, Comments等)
        summary_lines = summary.split('\n')
        cleaned_lines = []
        for line in summary_lines:
            # 過濾完整網址 (http:// 或 https://)
            if re.search(r'https?://', line):
                continue
            # 過濾標籤行 (Article URL:, Comments URL:, 文章網址:, 留言網址:)
            if re.search(r'(Article URL|Comments URL|文章網址|留言網址)', line, re.IGNORECASE):
                continue
            # 過濾 Hacker News 垃圾數據 (Points:, # Comments, Score:, etc.)
            if re.search(r'^(Points|Score|# Comments|Score:|分數|留言)', line.strip(), re.IGNORECASE):
                continue
            cleaned_lines.append(line)
        summary = '\n'.join(cleaned_lines).strip()
        
        # 如果過濾後空咗，用標題做 fallback
        if not summary:
            summary = headline
        
        # 生成 ID
        seq = len(news_items) + 1
        news_id = f"news_{date_str.replace('-', '')}_{seq:03d}"
        
        news_items.append({
            "id": news_id,
            "date": date_str,
            "headline_zh": headline,
            "headline_en": "",
            "summary_zh": summary[:200],
            "category": original_category,  # 使用 MD 檔案中原有的分類
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
    all_news = []
    
    for md_file in sorted(Path(POOL_DIR).glob("*.md")):
        date_str, news_items = parse_md_file(md_file)
        
        if not news_items:
            continue
        
        all_news.extend(news_items)
        converted += 1
        total_news += len(news_items)
        print(f"📄 {date_str}: {len(news_items)} 條新聞")
    
    # 使用 MD 檔案中原有的分類，無需 AI 重新分類
    # if all_news:
    #     classify_batch(all_news)
    
    # 按日期分組寫入
    by_date = {}
    for item in all_news:
        date = item["date"]
        if date not in by_date:
            by_date[date] = []
        
        # 合併已使用記錄
        if item["id"] in used_news:
            item["used_in_episodes"] = used_news[item["id"]]
        
        by_date[date].append(item)
    
    # 寫入 JSON
    for date_str, items in by_date.items():
        json_path = os.path.join(POOL_V2_DIR, f"{date_str}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    
    print(f"\n📊 總計: {converted} 個日期, {total_news} 條新聞")
    return total_news


if __name__ == "__main__":
    print("🔄 TechCanto 新聞池轉換器 + AI 分類")
    print("=" * 40)
    count = convert_all()
    print(f"\n✅ 完成！轉換咗 {count} 條新聞")
