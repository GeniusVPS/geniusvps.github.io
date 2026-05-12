#!/usr/bin/env python3
"""
TechCanto 新聞選擇器重新生成
從新聞池 markdown 檔案讀取新聞，過濾已用新聞，生成 index.html
"""

import json
import os
import re
import yaml
from datetime import datetime

# ─── 路徑設定 ───
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
POOL_DIR = os.path.join(BASE_DIR, "pool")
CONFIG_DIR = os.path.join(BASE_DIR, "config")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")

# 嘗試讀 episode registry 獲取下一集集數
EPISODE_REGISTRY_PATH = os.path.expanduser("~/.hermes/techcanto/config/episode_registry.yaml")


def load_used_news():
    """讀取已用新聞 ID 列表"""
    used_path = os.path.join(CONFIG_DIR, "used_news.json")
    try:
        with open(used_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # used_news.json 可以係 list 或 dict
        if isinstance(data, list):
            return set(data)
        elif isinstance(data, dict):
            return set(data.keys())
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def get_next_episode():
    """從 episode registry 獲取下一集集數"""
    try:
        with open(EPISODE_REGISTRY_PATH, "r", encoding="utf-8") as f:
            registry = yaml.safe_load(f)
        episodes = registry.get("episodes", [])
        if episodes:
            max_ep = max(ep.get("number", 0) for ep in episodes)
            return max_ep + 1
    except Exception:
        pass
    return 16  # 預設值


def parse_pool_files():
    """解析所有 pool markdown 檔案，提取新聞項目"""
    if not os.path.exists(POOL_DIR):
        print(f"❌ Pool directory not found: {POOL_DIR}")
        return []

    all_items = []
    seen_ids = set()

    # 按日期排序
    md_files = sorted([f for f in os.listdir(POOL_DIR) if f.endswith(".md")])

    for md_file in md_files:
        date_str = md_file.replace(".md", "")
        file_path = os.path.join(POOL_DIR, md_file)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"⚠️  無法讀取 {md_file}: {e}")
            continue

        # 解析新聞項目
        # 格式: ### N. Headline
        # - **來源：** [Source](URL)
        # - **時間：** HH:MM HKT
        # - **分類：** category
        # - **摘要：** summary

        blocks = content.split("### ")
        for block in blocks[1:]:  # 跳過第一個（標題之前）
            lines = block.strip().split("\n")
            if not lines:
                continue

            # 第一行包含編號同標題
            first_line = lines[0].strip()
            match = re.match(r"(\d+)\.\s+(.*)", first_line)
            if not match:
                continue

            num = int(match.group(1))
            headline = match.group(2).strip()

            # 生成 ID
            news_id = f"{date_str}_{num}"

            # 去重
            if news_id in seen_ids:
                continue
            seen_ids.add(news_id)

            # 解析其餘欄位
            source = ""
            url = ""
            category = "general"
            summary = ""

            for line in lines[1:]:
                line = line.strip()

                # 來源
                source_match = re.match(r"- \*\*來源：\*\* \[(.+?)\]\((.+?)\)", line)
                if source_match:
                    source = source_match.group(1)
                    url = source_match.group(2)

                # 分類
                cat_match = re.match(r"- \*\*分類：\*\* (.+)", line)
                if cat_match:
                    category = cat_match.group(1)

                # 摘要
                summ_match = re.match(r"- \*\*摘要：\*\* (.+)", line)
                if summ_match:
                    summary = summ_match.group(1)

            all_items.append({
                "id": news_id,
                "headline": headline,
                "source": source,
                "url": url,
                "summary": summary,
                "date": date_str,
                "category": category
            })

    return all_items


def generate_html(news_items, next_episode):
    """生成 index.html"""
    # 將新聞數據轉為 JSON
    news_json = json.dumps(news_items, ensure_ascii=False, indent=2)

    # HTML 模板 — JSON 數據注入，JavaScript 模板字元正常運作
    html = f'''<!DOCTYPE html>
<html lang="zh-HK">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TechCanto 新聞選擇器</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f4f4f9; color: #333; padding: 20px; }}
        .container {{ max-width: 800px; margin: 0 auto; }}
        h1 {{ text-align: center; color: #2c3e50; }}
        .news-card {{ background: white; border-radius: 8px; padding: 15px; margin-bottom: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); display: flex; align-items: flex-start; gap: 10px; transition: transform 0.2s; }}
        .news-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 8px rgba(0,0,0,0.15); }}
        .news-card input {{ margin-top: 5px; transform: scale(1.5); cursor: pointer; }}
        .news-content {{ flex: 1; }}
        .news-id {{ font-weight: bold; color: #e74c3c; margin-right: 5px; font-size: 0.9em; }}
        .news-headline {{ font-size: 1.1em; font-weight: 600; margin-bottom: 5px; }}
        .news-meta {{ font-size: 0.9em; color: #666; margin-bottom: 5px; }}
        .news-summary {{ font-size: 0.95em; color: #444; line-height: 1.4; }}
        .sticky-footer {{ position: fixed; bottom: 0; left: 0; right: 0; background: white; padding: 15px; box-shadow: 0 -2px 10px rgba(0,0,0,0.1); text-align: center; display: flex; justify-content: center; gap: 20px; align-items: center; }}
        .btn {{ padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 1em; font-weight: bold; transition: background 0.2s; }}
        .btn-primary {{ background-color: #3498db; color: white; }}
        .btn-primary:hover {{ background-color: #2980b9; }}
        .btn:disabled {{ background-color: #bdc3c7; cursor: not-allowed; }}
        .count {{ font-size: 1.2em; font-weight: bold; }}
        .toast {{ visibility: hidden; min-width: 250px; background-color: #333; color: #fff; text-align: center; border-radius: 4px; padding: 16px; position: fixed; z-index: 1; left: 50%; bottom: 80px; transform: translateX(-50%); }}
        .toast.show {{ visibility: visible; animation: fadein 0.5s, fadeout 0.5s 2.5s; }}
        @keyframes fadein {{ from {{bottom: 0; opacity: 0;}} to {{bottom: 80px; opacity: 1;}} }}
        @keyframes fadeout {{ from {{bottom: 80px; opacity: 1;}} to {{bottom: 0; opacity: 0;}} }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📰 TechCanto 新聞選擇器</h1>
        <p style="text-align: center; color: #666;">請選擇 6 則新聞生成新一集（第 {next_episode} 集）</p>
        <div id="news-list">
        </div>
    </div>

    <div class="sticky-footer">
        <span>已選擇：<span id="count" class="count">0</span> / 6</span>
        <button id="copy-btn" class="btn btn-primary" disabled>複製 ID 列表</button>
    </div>

    <div id="toast" class="toast">已複製到剪貼板！請貼上給 Hermes。</div>

    <script>
        const newsData = {news_json};
        let selectedIds = new Set();
        const MAX_SELECTION = 6;

        function renderNews() {{
            const list = document.getElementById('news-list');
            list.innerHTML = '';
            newsData.forEach(item => {{
                const card = document.createElement('div');
                card.className = 'news-card';
                card.innerHTML = `
                    <input type="checkbox" id="${{item.id}}" data-id="${{item.id}}" ${{selectedIds.has(item.id) ? 'checked' : ''}}>
                    <div class="news-content">
                        <div class="news-headline">
                            <span class="news-id">${{item.id}}</span> ${{item.headline}}
                        </div>
                        <div class="news-meta">${{item.source}} | ${{item.date}}</div>
                        <div class="news-summary">${{item.summary}}</div>
                    </div>
                `;
                list.appendChild(card);
            }});
            document.querySelectorAll('input[type="checkbox"]').forEach(cb => {{
                cb.addEventListener('change', function() {{
                    if(this.checked) {{
                        if(selectedIds.size < MAX_SELECTION) {{
                            selectedIds.add(this.dataset.id);
                        }} else {{
                            this.checked = false;
                            alert('最多只能選擇 6 條新聞');
                        }}
                    }} else {{
                        selectedIds.delete(this.dataset.id);
                    }}
                    updateCount();
                }});
            }});
        }}

        function updateCount() {{
            document.getElementById('count').textContent = selectedIds.size;
            document.getElementById('copy-btn').disabled = selectedIds.size === 0;
        }}

        document.getElementById('copy-btn').addEventListener('click', function() {{
            const ids = Array.from(selectedIds).join(', ');
            navigator.clipboard.writeText(ids).then(() => {{
                showToast();
            }});
        }});

        function showToast() {{
            const toast = document.getElementById('toast');
            toast.className = 'toast show';
            setTimeout(() => {{ toast.className = 'toast'; }}, 3000);
        }}

        renderNews();
    </script>
</body>
</html>'''

    return html


def main():
    print("=" * 50)
    print("🔄 TechCanto 新聞選擇器重新生成")
    print("=" * 50)

    # 1. 讀取已用新聞
    used_ids = load_used_news()
    print(f"📋 已用新聞 ID: {len(used_ids)} 個")

    # 2. 獲取下一集集數
    next_ep = get_next_episode()
    print(f"📺 下一集: 第 {next_ep} 集")

    # 3. 解析新聞池
    all_items = parse_pool_files()
    print(f"📰 新聞池總數: {len(all_items)} 條")

    # 4. 過濾已用新聞
    available = [item for item in all_items if item["id"] not in used_ids]
    print(f"✅ 可用新聞: {len(available)} 條")

    if not available:
        print("⚠️  無可用新聞，請先運行 fetch_news.py 更新新聞池")
        return

    # 5. 生成 HTML
    html = generate_html(available, next_ep)

    # 6. 寫入檔案
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ index.html 已更新 ({len(html)} bytes)")
    print(f"   包含 {len(available)} 條可用新聞")
    print(f"   下一集: 第 {next_ep} 集")
    print("=" * 50)


if __name__ == "__main__":
    main()
