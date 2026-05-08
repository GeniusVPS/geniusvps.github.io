# 📰 TechCanto News Pool

[![Fetch News](https://github.com/GeniusVPS/techcanto-news-pool/actions/workflows/fetch-news.yml/badge.svg)](https://github.com/GeniusVPS/techcanto-news-pool/actions/workflows/fetch-news.yml)

**TechCanto YouTube 頻道** 嘅自動新聞聚合系統。

---

## 🔧 功能

- **自動抓取** — 每 3 小時自動從多個 RSS 來源收集科技新聞
- **三層去重** — URL 精確匹配 + 標題相似度 + 內容 Hash，確保唔會重複
- **粵語摘要** — 每条新聞都有廣東話摘要，方便制作影片內容
- **每日分檔** — 按日期儲存，方便翻查

## 📂 結構

```
├── .github/workflows/
│   └── fetch-news.yml      # GitHub Actions 自動執行
├── scripts/
│   └── fetch_news.py       # 主程式
├── config/
│   ├── sources.json        # RSS 來源列表
│   ├── seen_urls.json      # URL 去重記錄
│   ├── seen_hashes.json    # 內容去重記錄
│   └── seen_titles.json    # 標題去重記錄
├── pool/                   # 每日新聞檔案
└── README.md
```

## 📡 新聞來源

| 來源 | 語言 | 分類 |
|------|------|------|
| TechCrunch | EN | 綜合 |
| The Verge | EN | 綜合 |
| Engadget | EN | 綜合 |
| 9to5Mac | EN | Apple |
| Wired | EN | 綜合 |
| Ars Technica | EN | 綜合 |
| ITmedia | JP | 綜合 |
| ASCII.jp | JP | 綜合 |
| AI News | JP | AI |
| Hacker News | EN | 綜合 |
| MIT Tech Review | EN | 綜合 |
| VentureBeat | EN | AI |

## ⚙️ 設定

### API Key

喺 GitHub Repo → Settings → Secrets and variables → Actions 入面設定：

| Secret | 說明 |
|--------|------|
| `LONGCAT_API_KEY` | LongCat API 金鑰 |

## 🚀 手動觸發

GitHub Actions → Fetch News → Run workflow

---

> Made with 🤖 for [TechCanto](https://youtube.com/@TechCanto)
