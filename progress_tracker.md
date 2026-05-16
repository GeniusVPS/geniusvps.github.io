# TechCanto v2.0 優化工程進度追蹤
# 最後更新：2026-05-17 00:05 HKT
# 此檔案用於跨會話追蹤進度，防止 AI 失憶

## 📊 整體狀態
- **開始日期：** 2026-05-16
- **當前階段：** Phase 5 完成，準備測試
- **v1 生產環境：** 完全未觸碰 ✅

---

## ✅ 已完成

### 基礎設施
- [x] v2 獨立環境 (`scripts-v2/`, `config-v2/`, `pool-v2/`)
- [x] Bug 修復：行號前綴、`summary_data` 未定義
- [x] 集數儀表板 (`dashboard.html`)
- [x] 長片製作規劃器 (`scripts/feature_planner.py`)

### Phase 1: 智能分類 + 粵語口語
- [x] 修復 `prompt` 未定義 bug（LongCat 永遠 NameError 嘅根源）
- [x] 新增 `build_translate_prompt()` — 智能分類 + 粵語翻譯
- [x] 新增 `call_llm_api()` — 通用 LLM API 調用
- [x] 新增 `parse_llm_response()` — 智能解析 JSON 響應
- [x] 翻譯成功率：100%（之前 0%）
- [x] 分類分佈：ai(17), hardware(12), business(12), software(11)

### Phase 2: 新聞評分系統
- [x] 新增 `SCORE_THRESHOLD = 4` — 評分閾值
- [x] 新增 `SCORING_CRITERIA` — 評分標準（新聞價值/相關性/獨特性/時效性）
- [x] 同一個 API call 完成翻譯 + 分類 + 評分
- [x] 評分過濾：低於 4 分嘅新聞自動排除
- [x] 評分排序：高分新聞優先
- [x] 新聞池輸出顯示 ⭐ 星號評分

### Phase 3: RSS 來源優化
- [x] 移除 8 個失效/超時來源（Gizmodo, MacRumors, ITmedia, AI News Japan, Anthropic, The Information, Reuters, Apple Insider）
- [x] 新增 3 個可靠來源（Security Week, BBC Technology, 9to5Google）
- [x] 最終來源：15 個，全部測試通過

### Phase 4: 統一系統架構
- [x] 統一 Config — `unified_config.json`
- [x] 共用設定模組 — `scripts/shared_config.py`
- [x] 新聞池 JSON 輸出 — `pool-v2/2026-05-16.json`
- [x] 新聞唯一 ID 系統 — `news_YYYYMMDD_NNN` 格式
- [x] 新聞池管理器 — `scripts/news_pool_manager.py`
- [x] 新聞選擇 Dashboard — `dashboard_news_selector.html`
- [x] API Server — `scripts/news_selector_server.py`

### Phase 5: GitHub Actions 整合 ✅ NEW
- [x] 建立 v2 GitHub Actions workflow — `.github/workflows/fetch_news_v2.yml`
- [x] 測試 v2 新聞池 JSON 輸出（2026-05-17）
- [x] 建立 Episode Creator 腳本 — `scripts/episode_creator.py`
- [x] 測試完整工作流（Ep.022 創建成功）
- [x] 新聞使用追蹤系統（`used_in_episodes` 欄位）

---

## ⏳ 待完成

### Phase 6: 長片自動製作
- [ ] 由新聞池直接生成長片腳本
- [ ] 自動組合短片為長片
- [ ] 長片儀表板

---

## 📁 關鍵檔案路徑

```
~/.hermes/geniusvps.github.io/
├── .github/workflows/
│   ├── fetch_news.yml              # v1 workflow（保留）
│   ├── fetch-news.yml              # v1 workflow（副本）
│   └── fetch_news_v2.yml           # v2 workflow（NEW）
├── scripts-v2/fetch_news_v2.py      # v2 新聞抓取（已優化）
├── config-v2/sources.json           # RSS 來源清單（15 個）
├── pool-v2/
│   ├── 2026-05-16.md              # 新聞池 Markdown
│   ├── 2026-05-16.json            # 新聞池 JSON（7 條）
│   ├── 2026-05-17.md              # 新聞池 Markdown（NEW）
│   └── 2026-05-17.json            # 新聞池 JSON（2 條，NEW）
└── progress_tracker.md             # 呢個檔案

~/.hermes/techcanto/
├── config/
│   ├── unified_config.json         # 統一設定檔
│   └── episode_registry.yaml       # 集數註冊表（21 集）
├── scripts/
│   ├── shared_config.py            # 共用設定模組
│   ├── news_pool_manager.py        # 新聞池管理器
│   ├── episode_creator.py          # 集數創建器（NEW）
│   └── news_selector_server.py     # Dashboard API Server
├── episodes/
│   └── ep022/                      # 第 22 集（NEW）
│       ├── news_items.yaml         # 新聞清單
│       └── metadata.json           # 元數據
└── dashboard_news_selector.html    # 新聞選擇 Dashboard
```

---

## 🔑 重要設定

- **評分閾值：** 4/10（低於呢個分數嘅新聞被過濾）
- **每次抓取上限：** 20 條新聞
- **RSS 超時：** 10 秒
- **LLM 超時：** 60-180 秒
- **API 優先級：** LongCat → LM Studio 本地 fallback
- **新聞 ID 格式：** `news_YYYYMMDD_NNN`（例如：news_20260516_001）
- **GitHub Actions：** 每 3 小時運行（UTC 0 */3 * * *）

---

## 📝 下次繼續時要記住

1. **v1 完全未觸碰** — 所有改動喺 v2 環境
2. **Phase 5 已完成** — GitHub Actions v2 workflow 已建立
3. **下一步：** Phase 6（長片自動製作）
4. **用戶溝通語言：** 粵語（Traditional Chinese）
5. **新聞池而家有 9 條新聞** — 7 條未用過，2 條已用於 Ep.022

---

## 🧪 測試指令

```bash
# 測試共用設定
cd ~/.hermes/techcanto && python3 scripts/shared_config.py

# 測試新聞池管理器
cd ~/.hermes/techcanto && python3 scripts/news_pool_manager.py

# 測試集數創建器
cd ~/.hermes/techcanto && python3 scripts/episode_creator.py 23

# 啟動 Dashboard Server
cd ~/.hermes/techcanto && python3 scripts/news_selector_server.py
# 訪問 http://localhost:8080

# 運行新聞池抓取
cd ~/.hermes/geniusvps.github.io && python3 scripts-v2/fetch_news_v2.py
```

---

## 📊 系統架構圖

```
RSS 來源 (15 個)
    ↓
GitHub Actions (每 3 小時)
    ↓
fetch_news_v2.py
    ↓
新聞池 JSON (pool-v2/2026-05-17.json)
    ↓
    ├── Dashboard 手動選擇
    └── Episode Creator 自動選擇
        ↓
    集數資料夾 (episodes/ep022/)
        ↓
    短片製作流程 (ComfyUI + TTS)
        ↓
    YouTube Shorts 上傳
```
