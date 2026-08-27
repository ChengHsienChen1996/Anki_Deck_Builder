# 專案概述

## 目的

「Anki 記憶引擎」（另一個獨立專案，純前端單檔 HTML）負責複習與 SM-2 排程，但它需要**現成的牌組 ZIP** 才能運作。手工製作卡片 CSV、聯想圖與語音檔耗時且無法規模化。

本專案提供一套本地 pipeline，把書本影像或純文字自動轉成完整牌組——含結構化卡片欄位、無文字的視覺記憶錨點圖、單字與例句語音——打包成記憶引擎可直接載入的 ZIP。

### 與記憶引擎的關係

```
┌──────────────────────┐         ┌──────────────────────┐
│  anki-deck-builder   │  ZIP    │  Anki 記憶引擎        │
│  (本專案・Python)     │ ──────→ │  (單檔 HTML・純前端)  │
│  重量級本地處理        │         │  免安裝・瀏覽器執行    │
└──────────────────────┘         └──────────────────────┘
```

**唯一交接介面是 ZIP 檔案格式。** 本專案不需知道引擎的內部實作（SM-2、複習模式等），引擎也不需知道卡片如何產生。

## 五階段流程

```
[輸入源]
   │
   ├─ 圖像 / PDF ──→ ① OCR ──→ 原始文字
   └─ 純文字 ────────────────────┘
                                 │
                                 ▼
                       ② LLM 抽取整理
              （結構化欄位 + 缺失補全 + 生成 image_prompt）
                                 │
                                 ▼
                       ③ ComfyUI 批量圖生成
                                 │
                                 ▼
                       ④ VOXCPM2 語音生成（單字、例句各一檔）
                                 │
                                 ▼
                       ⑤ 路徑整合與打包
                                 │
                                 ▼
                            [deck.zip]
```

## 目標

- 支援**任意學習領域**，領域由使用者於執行時指定，程式不內建任何領域知識
- 五階段皆可**獨立執行**，亦可串成單一指令跑完全程
- 長時間任務**可中斷續作**，重啟後跳過已完成項目
- 單項失敗**不中斷整批**，事後可只重跑失敗項
- 對個別卡片的圖不滿意時，可**只改該列 prompt 重生一張**，不重跑整個 LLM 抽取
- 所有外部服務位址、模型名稱、ComfyUI 節點指定，**一律由 `.env` 驅動**

## 非目標

以下明確**不做**，避免範圍擴張：

| 非目標 | 說明 |
|--------|------|
| 不實作複習功能 | SM-2、排程、複習介面全部屬於記憶引擎，本專案只產出 ZIP |
| 不實作 OCR / TTS / 圖生成模型 | 一律呼叫外部服務，本專案只負責串接與流程編排 |
| 不做雲端部署 | 純本地工具，不考慮多使用者、認證、伺服器託管 |
| 不做卡片內容的品質評分 | LLM 抽取結果由使用者於 Web UI 人工檢視，程式不自動判斷好壞 |
| 不做增量式牌組合併 | 每次執行產出獨立 ZIP，不處理與既有牌組的差異比對或合併 |
| 不保留舊版 emoji 合成圖 | 聯想圖一律由 ComfyUI 生成，不保留先前的 emoji 拼貼方案作為 fallback |
| 不支援 GUI 以外的排程自動化 | 不做 cron、watch 資料夾等自動觸發機制 |

## 技術棧

| 組件 | 選型 | 理由 |
|------|------|------|
| 語言 | Python 3.11+ *(待確認：最低版本依 uv 與相依套件實測決定)* | 生態完整，與既有工具鏈一致 |
| 套件管理 | **uv** | 安裝快速、鎖檔可靠、單一工具涵蓋虛擬環境與相依解析 |
| **LLM / OCR SDK** | **`agent_factory`（git submodule）** | 自製 SDK，已封裝 agent 建立、prompt 管理、structured output、三維度速率限制 |
| LLM 底層 | `openai-agents`（由 agent_factory 引入） | 支援任何 OpenAI-compatible endpoint，不鎖定供應商 |
| 設定管理 | pydantic-settings | 型別驗證與 `.env` 載入一次到位，缺項報錯明確 |
| CLI 框架 | **argparse** | 標準庫，零額外相依，子命令結構足夠 |
| HTTP client | httpx | 原生支援 async，符合 async 優先原則 |
| 檔案 I/O | aiofiles | 中間 CSV 的異步讀寫 |
| Web 後端 | **FastAPI** | 與 Gradio 整合成熟，async 原生 |
| Web UI | **Gradio** | 快速產出可用介面，免自寫前端，適合本專案的人工檢視需求 |
| PDF 轉圖 | **pypdfium2** *(待確認：見下方說明)* | 單一 wheel、無系統相依、授權寬鬆 |
| TTS | **voxcpm**（本機 Python 套件） | 直接在本行程內以 GPU 推論，非 HTTP 服務；支援參考音檔 voice cloning |
| 測試 | pytest + pytest-asyncio | 依 [testing-strategy.md](testing-strategy.md) |
| Lint / Format | ruff | 依 [coding-style.md](coding-style.md) |

### PDF 轉圖選型說明（待確認）

需求是「以減少需求套件為優先」，且輸入不一定是 PDF（可能直接是圖片）。

- **pypdfium2**：單一 wheel、無系統相依、BSD/Apache 授權。**建議選項。**
- pymupdf：功能強但為 AGPL 授權，商用需留意。
- pdf2image：需系統另裝 poppler，違反「減少需求套件」原則。

> **開工時需驗證**：pypdfium2 的實際 API 與渲染品質是否滿足 OCR 需求。若不符再回頭討論。

## 硬體與資源預算

| 項目 | 規格 |
|------|------|
| GPU | NVIDIA RTX 3090，**24 GB VRAM** |
| 生成解析度 | 1024 × 576（16:9，對應記憶引擎卡片版面） |

### VRAM 分配原則

| 服務 | 預估佔用 | 說明 |
|------|----------|------|
| ComfyUI（SDXL 1024×576） | 待實測 | `COMFYUI_BATCH_SIZE` 預設 `4`，**待確認：需依實際 workflow 實測調整** |
| VOXCPM2 | 權重約 **4.96 GB**（`model.safetensors` 4.58 GB + `audiovae.pth` 377 MB），推論期另需活動記憶體 | **在本專案行程內**以 GPU 推論，不是獨立服務——無法靠「關掉服務」讓出 VRAM，必須與 ComfyUI 錯開執行 |

### Ollama 模型的 `num_ctx` 會決定成敗（2026-08-21 實測）

本地模型若沒有**完整**載入 VRAM，速度會掉一個數量級，長輸入的請求直接逾時。
罪魁禍首通常是 Modelfile 的 `num_ctx`——context 開太大時，KV cache 的預留量
會把模型權重擠到 CPU。

`gemma4_31b_q4_K_M-optimized` 的實測（RTX 3090 / 24 GB）：

| `num_ctx` | 權重在 VRAM | prompt 處理 | 輸出速度 |
|-----------|------------|------------|---------|
| 262144（原設定） | 11.4 / 24.1 GB | 14 tok/s | **2.5 tok/s** |
| 8192 | 20.3 / 20.3 GB（100%） | 142 tok/s | **34.2 tok/s** |

差距 13.7 倍。在 2.5 tok/s 下，一頁教材的抽取（prompt 約 3.8K token、
輸出約 2K token）要 15–20 分鐘，超過 API client 的預設逾時。

**檢查方式**：

```bash
curl -s http://localhost:11434/api/ps | python3 -m json.tool   # 比對 size 與 size_vram
ollama show --modelfile <model>                                # 看 PARAMETER num_ctx
```

`size_vram` 明顯小於 `size` 就是沒全載。本專案單次呼叫的 context 需求約 8K，
`num_ctx` 設 16384 已足夠且仍有 VRAM 餘裕。

**執行策略**：階段 ③（圖）與階段 ④（音）**依序執行，不併行**，避免兩個模型同時佔用 VRAM 導致 OOM。`run-all` 已依此順序設計。

> **開工時需驗證**：以實際 workflow 測試 batch size 1 / 2 / 4 / 8 的 VRAM 佔用與吞吐，決定預設值。

### 階段間的 VRAM 讓渡（待各 phase 實測後決定）

24 GB 放不下全部東西，光抽取模型就佔掉 20.3 GB：

| 階段 | 佔用者 | 實測／預估 |
|------|--------|-----------|
| ② extract | Ollama 的 `gemma4_31b_q4_K_M`（`num_ctx=8192`） | **20.3 GB**（已實測） |
| ③ image | ComfyUI（SDXL 1024×576） | 待實測 |
| ④ audio | `voxcpm`，**在本專案行程內** | 權重 4.96 GB + 推論期活動記憶體 |

**關鍵事實**：抽取模型一個人就佔掉 85% 的卡。Ollama 預設會讓模型常駐一段時間才卸載，
所以 `run-all` 從 extract 走到 image 時，很可能 ComfyUI 還沒開始就已經沒有 VRAM 可用。

初步方向（**不預先實作，各 phase 實測後再決定**）：

- **extract → image 之間需要主動讓渡**。已實測可行的卸載方式（2026-08-21）：

  ```bash
  curl -s http://localhost:11434/api/generate \
       -d '{"model":"<model>","keep_alive":0}'
  # → {"done":true,"done_reason":"unload"}
  ```

  不需要 `prompt` 欄位，也不需要 `ollama` CLI——純 HTTP，本專案已有 httpx。
  實測 20.3 GB 在 0.5 秒內完全釋放（`nvidia-smi` 由 21.3 GB 降到 1.0 GB）。

  **實作時的坑**：回應回來的當下 `/api/ps` **仍可能列出該模型**，卸載相對於回應是
  非同步的。要確認真的讓出 VRAM，必須輪詢 `/api/ps` 直到清空，不能把回應當成完成訊號。

  但這是 Ollama 專屬手段，而 `agents.yaml` 可指向任何 OpenAI-compatible 供應商——
  若要做，應設計成**可選、由設定驅動**的收尾動作（例如 `LLM_UNLOAD_URL` 之類的設定，
  留空即不執行），不可寫死進 `llm_client.py`。
- **image → audio 之間可能不需要讓渡**：兩者相加預估仍在 24 GB 內，
  但 ComfyUI 是常駐服務、模型是否留在 VRAM 取決於它自己的策略，需 Phase 4 實測。
- 各 phase 的驗收流程都要求以 `nvidia-smi` 記錄實際佔用，累積數據後再回頭決定策略。

## 環境與工具鏈

### 安裝

```bash
# 取得 submodule（agent_factory）
git submodule update --init --recursive

# 建立虛擬環境並安裝
uv venv
uv pip install -e ".[dev]"

# 執行
uv run anki-builder --help
```

> `agent_factory` 以 git submodule 引入，clone 後必須先執行 `submodule update`，否則 `import agent_factory` 會失敗。

### 設定檔

- `.env`：實際設定，`.gitignore` 排除，**完全不納入版控**
- `.env.example`：範例與預設值，納入版控且會進 `main`，新增變數時同步更新

### 版控分區

本專案的檔案分區依 [project-structure.md](project-structure.md) 的 Zone 1 / Zone 2 規範。本專案特有目錄的歸屬：

| 目錄 | 分區 | 說明 |
|------|------|------|
| `src/` `tests/` `docs/` `prompts/` `workflows/` `scripts/` `.githooks/` | Zone 1 | 正式產出，照常合併進 `main` |
| `CLAUDE.md` `.claude/` `.agent/` `logs/` | Zone 2 | 於 `dev_ai` 保有本地版控，**永不進 `main`** |
| `work/` `output/` | 不版控 | `.gitignore` 排除，屬執行期產物 |
| `.claude/settings.local.json` | **無版控** | 被全域 gitignore 擋住，連 `dev_ai` 都沒有歷史，須以 repo 外快照備份 |

`workflows/` 存放使用者自帶的 ComfyUI workflow JSON，屬專案正式資產，歸 Zone 1。

> `.claude/settings.local.json` 是本專案唯一需要**手動備份**的檔案。每次開工前依 [git-workflow.md](git-workflow.md)〈Claude Code 權限設定備份〉快照至 `~/.claude/settings-backups/anki-deck-builder/`。

完整變數清單見 [architecture.md](architecture.md) 的「設定參數化」章節。

### 外部服務相依

本專案需以下服務可用（依 phase 而定）：

| 相依 | 形式 | 用於 | Phase | 規格狀態 |
|------|------|------|-------|----------|
| `agent_factory`（OpenAIAgentsSDKFactory） | git submodule | LLM 抽取、OCR | 1, 2 | ✅ README 已提供 |
| Gemma 4 endpoint | OpenAI-compatible API | 抽取整理；`vision_direct` 下兼讀取 | 1 | 由 `agents.yaml` 指定；**多模態支援待查證** |
| GLM-OCR 變體 endpoint | OpenAI-compatible API | 影像轉文字（`two_stage`） | 2 | 由 `agents.yaml` 指定 |
| ComfyUI | 本地 HTTP 服務 | 聯想圖生成 | 3 | ⬜ workflow 待提供 |
| VOXCPM2 | **本機 Python 套件**（`voxcpm`） | 語音生成 | 4 | ✅ 已確認（2026-08-22 實查） |

`agent_factory` 已提供 agent 建立、prompt 載入、structured output 解析與**三維度速率限制**，本專案不重複實作，僅寫一層薄適配層。詳見 [architecture.md](architecture.md)〈LLM 與 OCR 的底層：agent_factory〉。

ComfyUI 的 workflow 尚未提供，以 Protocol 抽象隔離。

VOXCPM2 的規格已於 2026-08-22 實查確認，見 [architecture.md](architecture.md)〈設定參數化〉的 VOXCPM2 區塊與 [phase-4-audio.md](../.agent/plans/phase-4-audio.md)〈前置條件〉。
