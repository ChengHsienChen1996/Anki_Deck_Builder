# 架構設計

> 通用的 src layout、命名、async 原則、測試方式，分別以 [project-structure.md](project-structure.md)、[coding-style.md](coding-style.md)、[testing-strategy.md](testing-strategy.md) 為準。
> 本文件只寫**本專案獨有的架構決策**，以及通用規則在本專案的具體落實方式。

---

## 架構約束

以下五條為本專案的設計底線，違反會使核心機制失效。

### 約束 1：外部服務一律經 Protocol 隔離

**做什麼**：所有外部服務（LLM、OCR、ComfyUI、TTS）在 `clients/protocols.py` 定義 Protocol 抽象介面，`stages/` 只依賴 Protocol，不依賴具體實作。

**為什麼**：三項外部介面的實際規格尚未提供。以 Protocol 隔離後，Phase 1 可在介面未定時先行開發；規格補齊後只需實作 Protocol，上層完全不動。這是通用文檔「依賴注入」原則在本專案的具體落實。

**推論**：實際介面與 Protocol 預留簽章不符時，**調整 Protocol 而非上層邏輯**。

### 約束 2：中間 CSV 必須原子寫入

**做什麼**：中間 CSV 每次更新採「寫暫存檔 → `os.replace` rename」，不得直接覆寫原檔。

**為什麼**：圖生成 100 張可能耗時 30 分鐘。若寫入過程中斷（Ctrl-C、OOM、斷電），直接覆寫會導致整份工作檔損毀，前面所有階段的成果一併喪失。

### 約束 3：失敗記錄後跳過，不中斷整批

**做什麼**：單列處理失敗時，錯誤寫入該列的 `*_error` 欄位、狀態設為 `failed`，**繼續處理下一列**。

**為什麼**：批次處理中少數卡片失敗是常態（OCR 辨識不良、生成逾時、服務短暫不可用）。中斷整批會浪費已完成的工作，且無法得知還有多少其他項目也會失敗。

**推論**：`stages/` 內的迴圈不得讓單列例外向外傳播。

### 約束 4：分層依賴單向

**做什麼**：

```
介面層  cli.py / web/          ← 只做參數解析、進度顯示、使用者互動
   ↓
流程層  stages/                ← 單一階段的完整邏輯與狀態流轉
   ↓
狀態層  state/                 ← 中間 CSV 讀寫與待處理列篩選
   ↓
服務層  clients/               ← 封裝外部 API、錯誤轉為自訂例外
   ↓
型別層  schemas/               ← 資料結構定義與驗證
```

**為什麼**：CLI 與 Web UI 是**平行介面**，兩者呼叫 `stages/` 的同一組函式。若業務邏輯散落在介面層，兩邊會產生行為不一致。

**推論**：
- `stages/` 不得直接發 HTTP 請求，一律經 `clients/`
- `state/` 不得知道任何階段的業務語義
- `web/` 不得重複實作 `stages/` 已有的邏輯

### 約束 5：外部參數零硬編碼

**做什麼**：所有 endpoint、模型名稱、**ComfyUI 節點 ID 與欄位名**、路徑，一律從 `config.py` 取得。

**為什麼**：ComfyUI workflow 由使用者自帶，節點結構因人而異。硬編碼節點 ID 會使工具綁死單一 workflow，換一份就要改程式。

**推論**：`comfyui_client.py` 中不得出現任何字面量節點 ID。

---

## 模組結構

只列本專案獨有的部分，通用 src layout 見 [project-structure.md](project-structure.md)。

```
專案根目錄/
├── agents.yaml               # agent_factory 的 agent 宣告（見下方說明為何置於根目錄）
├── prompts/                  # agents.yaml 以 ${__dir__}/prompts/... 引用
├── workflows/                # 使用者自帶的 ComfyUI workflow JSON
└── src/
    ├── agent_factory/        # git submodule：OpenAIAgentsSDKFactory
    │                         # 注意：套件本體在 src/agent_factory/src/agent_factory/
    │                         # 以 editable 路徑相依安裝，`import agent_factory` 命中內層
    └── anki_deck_builder/
        ├── cli.py                # argparse 子命令與 dispatch，asyncio.run 進入點
        ├── config.py             # pydantic-settings 載入 .env，巢狀分組存取
        ├── exceptions.py         # 專案自訂例外
        ├── schemas/
        │   ├── card.py           # CardRow：中間 CSV 單列完整型別
        │   ├── status.py         # StageStatus 列舉
        │   └── extract_output.py # LLM structured output schema（供 agents.yaml 的 output_schema 引用）
        ├── state/
        │   ├── store.py          # 中間 CSV 異步讀寫（原子寫入）
        │   └── selector.py       # 待處理列篩選、狀態更新、錯誤寫入
        ├── clients/
        │   ├── protocols.py      # 四個 Protocol 抽象定義
        │   ├── llm_client.py     # agent_factory 的薄適配層（LLM）
        │   ├── ocr_client.py     # agent_factory 的薄適配層（OCR）
        │   ├── comfyui_client.py # ComfyUI HTTP API
        │   └── tts_client.py     # VOXCPM2
        ├── stages/
        │   ├── base.py           # 階段共用骨架與 registry
        │   ├── ocr.py            # 階段 ①
        │   ├── extract.py        # 階段 ②
        │   ├── image.py          # 階段 ③
        │   ├── audio.py          # 階段 ④
        │   └── pack.py           # 階段 ⑤
        └── web/
            ├── server.py         # FastAPI app
            └── ui.py             # Gradio 介面，掛載於 FastAPI
```

### 為何 `agents.yaml` 置於專案根目錄

`agent_factory` 的 YAML 支援 `${__dir__}` 變數，**解析為 YAML 檔所在目錄**。將 `agents.yaml` 放在根目錄後，`${__dir__}/prompts/extract_cards.md` 恰好指向 [project-structure.md](project-structure.md) 規範的根層 `prompts/`，兩者自然對齊，不需額外的相對路徑計算。

若改放 `src/anki_deck_builder/` 下，`${__dir__}/prompts/` 會指向 `src/anki_deck_builder/prompts/`，與通用規範衝突。

### 專案獨有目錄

分區依 [project-structure.md](project-structure.md) 的 Zone 1 / Zone 2 定義。

| 目錄 | 用途 | 分區 |
|------|------|------|
| `prompts/` | LLM system prompt 與 image prompt 風格模板 | Zone 1 |
| `workflows/` | 使用者自帶的 ComfyUI workflow JSON | Zone 1 |
| `tests/fixtures/` | 對齊樣板：書頁照片、OCR 原文、理想抽取結果（黃金樣本） | Zone 1 |
| `src/agent_factory/` | git submodule，LLM/OCR 呼叫的底層 SDK | Zone 1（submodule 指標） |
| `agents.yaml` | agent 宣告設定 | Zone 1 |
| `work/` | 中間 CSV、OCR 暫存、生成中的媒體檔 | 不版控（`.gitignore`） |
| `output/` | 最終打包的 ZIP | 不版控（`.gitignore`） |

> `work/` 與 `output/` 是**執行期產物**，以 `.gitignore` 完全排除，與 Zone 2 的「本地版控但不進 main」性質不同。

---

## 核心機制：雙路徑輸入模式

影像類輸入有兩條路徑可走，由 `.env` 的 `INGEST_MODE` 決定。純文字輸入不受此設定影響，一律直接進階段 ②。

```
純文字 ─────────────────────────────────→ ② extract ──→ ③ ④ ⑤

影像 / PDF ┬─ two_stage ──→ ① ocr ──→ ② extract ──→ ③ ④ ⑤
           │                (GLM-OCR)   (Gemma 4)
           │
           └─ vision_direct ──────────→ ② extract ──→ ③ ④ ⑤
                                        (Gemma 4 直接讀圖)
```

### 兩種模式的取捨

| | `two_stage`（預設） | `vision_direct` |
|---|---|---|
| 讀取模型 | GLM-OCR（專門調參） | Gemma 4 |
| 中介產物 | `raw_text` 可檢視 | 無中介，黑箱 |
| 誤差傳遞 | OCR 誤判會傳到抽取 | 無此問題 |
| 調整 prompt 重跑成本 | 低——只重跑 extract | 高——每次重送影像 |
| 版面資訊 | 轉純文字時部分失真 | 完整保留 |
| 模型負擔 | 各司其職 | 單一模型兼顧讀取與抽取 |

**預設為 `two_stage`** 的理由：GLM-OCR 是為讀取專門調參的模型，在「讀」這件事上預期優於通用模型；且中介可檢視大幅降低除錯與迭代成本。

`vision_direct` 適合的場景：版面複雜、欄位對齊或標音位置關係重要、OCR 轉文字後資訊明顯失真時。

### 實作方式

兩條路徑**共用同一個 `stages/extract.py`**，差異只在兩處：

1. **輸入內容**：`two_stage` 送 `raw_text` 字串；`vision_direct` 送影像 content block（`LimitAgentRunner.run(input_=...)` 接受 `str | list`）
2. **使用的 agent**：`ExtractAgent` vs `VisionExtractAgent`，兩者在 `agents.yaml` 分別宣告，共用同一個 `output_schema`

`vision_direct` 模式下 `raw_text` 留空——該欄位是否為空即可判斷這張卡走的是哪條路徑。

### 前提條件（待確認）

`vision_direct` 需要**抽取模型本身支援影像輸入**。

> **開工時必須查證**：Gemma 4 是否支援多模態影像輸入。若不支援，`vision_direct` 無法使用該模型，需停下來與使用者討論——改用其他 VLM，或維持 `two_stage` 單一路徑。
>
> agent_factory 本身已確認支援影像輸入，非阻礙因素。

---

## 核心機制：中間 CSV 狀態機

一份中間 CSV 貫穿五階段，同時是**資料載體**與**狀態機**。

```
階段①      階段②           階段③        階段④        階段⑤
 OCR   →  Extract    →    Image    →   Audio    →    Pack
  │          │              │            │             │
  ▼          ▼              ▼            ▼             ▼
raw_text  內容欄位       image_front  audio_front   移除中間欄位
          image_prompt                audio_back    輸出 ZIP
          reading
  └──────────┴──────────────┴────────────┘
         同一份 work/cards.csv 逐步增長
```

### 欄位分類

#### (a) 引擎 schema 欄位——打包後保留

與記憶引擎 CSV 格式完全一致：

`card_id` `deck` `card_type` `front` `back` `hint` `image_front` `image_back` `audio_front` `audio_back` `video` `example` `mnemonic` `note` `tags` `category` `difficulty` `source` `created_at` `last_reviewed` `interval` `ease_factor` `review_count`

| 欄位群 | 填寫階段 |
|--------|----------|
| `card_id` `deck` `card_type` `front` `back` `hint` `example` `mnemonic` `note` `tags` `category` `difficulty` `source` | ② extract |
| `image_front` | ③ image |
| `audio_front` `audio_back` | ④ audio |
| `image_back` `video` | 保留空白，暫不使用 |
| `created_at` `last_reviewed` `interval` `ease_factor` `review_count` | **一律留空**，由記憶引擎管理 |

#### (b) 製卡中間欄位——打包時移除

| 欄位 | 型別 | 填寫階段 | 說明 |
|------|------|----------|------|
| `raw_text` | str | ① | OCR 原始文字片段 |
| `ocr_source_page` | int | ① | 來源頁碼，供追溯 |
| `reading` | str | ② | 讀音（假名／拼音），供 TTS 使用 |
| `image_prompt` | str | ② | 英文圖生成 prompt |
| `tts_front_text` | str | ② | `audio_front` 要唸的文字 |
| `tts_back_text` | str | ② | `audio_back` 要唸的文字 |

> `image_prompt` **必須保留於工作檔**——這是「只改 prompt 重生單張圖」的關鍵。

#### (c) 狀態欄位——打包時移除

| 狀態欄位 | 錯誤欄位 | 階段 |
|----------|----------|------|
| `ocr_status` | `ocr_error` | ① |
| `extract_status` | `extract_error` | ② |
| `image_status` | `image_error` | ③ |
| `audio_front_status` | `audio_front_error` | ④ |
| `audio_back_status` | `audio_back_error` | ④ |

> `audio_front` 與 `audio_back` 狀態**獨立**，任一失敗不影響另一個。

### 狀態流轉

```python
class StageStatus(str, Enum):
    PENDING = "pending"
    DONE    = "done"
    FAILED  = "failed"
```

```
    pending ──成功──→ done
       │               ▲
     失敗              │ 重跑成功
       ▼               │
    failed ────────────┘
```

### 篩選規則

| 條件 | 納入的狀態 |
|------|-----------|
| 預設 | `pending` + `failed` |
| `--only-failed` | 僅 `failed` |
| `--force` | 全部（含 `done`） |

> `--force` 與 `--only-failed` 互斥，同時指定時於參數解析階段報錯。

### 針對性重跑

| 情境 | 操作 |
|------|------|
| 中斷後續作 | 直接重跑該階段，`done` 自動跳過 |
| 只重跑失敗項 | 直接重跑，或加 `--only-failed` |
| 改 prompt 重生單張圖 | 改 `image_prompt` → `image_status` 設回 `pending` → 重跑 `image` |
| 強制全部重來 | 加 `--force` |

### CSV 格式規格

| 項目 | 規格 |
|------|------|
| 編碼 | UTF-8 with BOM |
| 分隔符 | 半形逗號 |
| 引號 | 半形雙引號，含逗號／換行／引號時必須包覆 |
| 欄位內換行 | 使用 `\n` 字面值 |
| 欄位順序 | (a) → (b) → (c)，須穩定不變 |

---

## LLM 與 OCR 的底層：agent_factory

LLM 與 OCR 呼叫皆建立在 **`agent_factory`**（OpenAIAgentsSDKFactory）之上——這是以 `openai-agents` SDK 為基礎的自製 SDK，以 git submodule 引入。

> **開工前必讀**：`src/agent_factory/README.md`。以下僅摘錄本專案用得到的部分，完整 API 以該 README 為準。

### 它已經提供的能力（本專案不重複實作）

| 能力 | 由 agent_factory 提供 | 本專案要做的事 |
|------|----------------------|---------------|
| Agent 建立與管理 | `create_agent_factory()` → `get_agent_by_name()` | 寫 `agents.yaml` |
| System prompt 載入 | YAML `instruction_file_path` 指向 Markdown | 寫 `prompts/*.md` |
| Structured output 解析 | YAML `output_schema` 指向 Pydantic model | 定義 `ExtractOutput` |
| **速率限制（TPM/RPM/RPD）** | `LimitAgentRunner.run()` 內建 `limits_guard_multi` | **不實作**，只需在 `agents.yaml` 宣告 `model_params.limits` |
| 設定驗證 | `AgentConfigLoader` 的 Pydantic 驗證 | 無 |
| 多供應商切換 | YAML 的 `client.base_url` / `model` | 在 YAML 分別宣告 LLM 與 OCR 的 model 區塊 |

> **重要修正認知**：速率限制、prompt 載入、structured output 解析**都不是本專案要寫的**。`clients/llm_client.py` 只是一層很薄的適配層，把 agent_factory 的呼叫結果轉成 `stages/` 需要的形狀，並把外部例外轉為專案自訂例外。

### 標準呼叫形式

```python
from agent_factory.core import create_agent_factory
from agent_factory.limit_runner import LimitAgentRunner

factory = create_agent_factory()               # 讀 YAML_SETTINGS_FILE 環境變數
agent   = factory.get_agent_by_name("ExtractAgent")
runner  = LimitAgentRunner(agent=agent)
result  = await runner.run(input_=raw_text)
output  = result.final_output                  # 已依 output_schema 解析為 Pydantic model
```

### 速率限制配額寫在 `agents.yaml`

配額的設定來源優先序為 **agent YAML 的 `model_params.limits` > `MODEL_LIMITS` > `DEFAULT_POLICY`**（見 submodule README〈設定來源與優先序〉）。

**本專案採最高優先序的第一項**：每個模型直接在 `agents.yaml` 宣告 policy，不改 submodule、不注入 `MODEL_LIMITS`。

```yaml
model_params:
  model: gemma4_31b_q4_K_M-optimized
  limits:
    policy: concurrency_only     # 本地 Ollama：無帳單，TPM／RPM 不對應真實約束
```

本專案兩個模型（抽取用 `gemma4_31b_q4_K_M-optimized`、OCR 用 `glm-ocr-optimized:latest`）皆走本地 Ollama，一律 `concurrency_only`，實際節流只剩 `GLOBAL_CONCURRENCY`。

> **先前的錯誤描述已修正**：舊版本文件寫「未登錄 `MODEL_LIMITS` 會拋 `KeyError`」。實際行為是套用 `DEFAULT_POLICY`（預設 `concurrency_only`，可由 `LIMIT_DEFAULT_POLICY` 覆寫）並對該模型發出**一次** warning，**不拋例外**。已於 2026-08-21 實測 `agent_factory.rate_limiter.limits_parameters.DEFAULT_POLICY == LimitPolicy.CONCURRENCY_ONLY` 確認。
>
> 唯一會拋例外的情況是 `enforced` 策略缺 `TPM` 或 `RPM`（啟動時驗證失敗），以及 `enforced` 模型單次預扣量超過其 `TPM`（`AsyncTokenBucket.acquire()` 拋 `ValueError`）。

---

## 介面契約：clients/protocols.py

四個 Protocol 於 **Phase 1 全部定義完成**，後續 phase 只實作不新增。

```python
from typing import Protocol, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClientProtocol(Protocol):
    async def run_agent(self, agent_name: str, input_: str) -> BaseModel:
        """以 agents.yaml 中宣告的 agent 執行一次呼叫。

        prompt、模型、structured output schema 全部由 agents.yaml 決定，
        呼叫端只給 agent 名稱與輸入文字。
        回傳值已由 agent_factory 依 output_schema 解析為 Pydantic model。
        """
        ...


class OCRClientProtocol(Protocol):
    async def recognize(self, image_b64: str) -> str:
        """影像以 base64 字串傳入，避免路徑編碼與跨平台問題。

        待確認：openai-agents 的 input_ 接受 str | list，
        多模態影像輸入的實際 content block 格式需開工時查證。
        """
        ...


class ImageGenClientProtocol(Protocol):
    async def generate(
        self,
        positive_prompt: str,
        seed: int | None = None,
    ) -> bytes: ...


class TTSClientProtocol(Protocol):
    async def synthesize(self, text: str) -> bytes:
        """合成一段語音，回傳編碼後的音檔 bytes。

        音色、生成參數、模型路徑全部來自 config.py（約束 5），不進簽章——
        它們對整套牌組一致，放進簽章會讓每個呼叫端都得傳一次。
        """
        ...
```

> `TTSClientProtocol` 已依 2026-08-22 實查的 `voxcpm` API 收斂：原先預留的 `speaker_id` / `language` / `speed` 三個參數在該套件中**並不存在**，已移除。
>
> `ImageGenClientProtocol` 的簽章仍為預留設計，實際 workflow 提供後依情況調整 Protocol，**不改上層**。

---

## 擴充性設計：stages/base.py

**原則**：新增階段時「加檔案，不改核心」。

`stages/base.py` 提供：
- 階段共用骨架：讀取待處理列 → 逐列處理 → 更新狀態 → 原子寫回
- 階段 registry：以裝飾器註冊，`cli.py` 依名稱查表 dispatch

各階段模組只需實作「單列如何處理」，共用的狀態流轉、失敗跳過、原子寫入邏輯不重複實作。

> **對 AI 的限制**：新增階段時只准在 `stages/` 加檔案並註冊，**不准修改 `base.py` 的骨架邏輯**。若必須修改，停下來與使用者討論。

---

## 設定參數化

### 原則

1. 所有外部參數從 `config.py` 取得，程式碼零硬編碼（約束 5）
2. `config.py` 使用 pydantic-settings，**巢狀分組存取**，避免扁平命名空間：
   ```python
   settings.agent_factory.yaml_settings_file
   settings.comfyui.base_url
   settings.comfyui.nodes.positive_node_id
   settings.tts.reference_wav
   ```
3. 金鑰類欄位在 log 與 Web UI 顯示時**必須遮罩**

### 啟動時驗證

`config.py` 載入時必須驗證：

| 檢查項 | 失敗行為 |
|--------|----------|
| 必填變數存在 | 拋 `ConfigurationError`，訊息指出缺少的變數名 |
| 數值型變數可轉型 | 同上，指出變數名與收到的值 |
| `COMFYUI_WORKFLOW_PATH` 檔案存在 | 僅在 `image` 階段檢查，避免其他階段被無關設定阻擋 |

> **階段性驗證**：不在啟動時檢查所有服務可達性。各階段只驗證自己需要的設定，讓 Phase 1 不受未設定的 ComfyUI／TTS 阻擋。

### 環境變數清單

#### agent_factory（LLM + OCR 共用）

變數集合由 `agent_factory` 定義，見其 README。本專案照用，**不自行新增 endpoint／model 類變數**：

```env
OPENAI_API_KEY=
YAML_SETTINGS_FILE=agents.yaml
GLOBAL_CONCURRENCY=6
RPM=200
TPM=30000
```

> **重要**：endpoint 與模型名稱**不放 `.env`**，而是寫在 `agents.yaml` 的 `client.base_url` 與 `model` 欄位。這是 agent_factory 的設計——同一份 `.env` 可搭配不同 YAML 切換供應商。
>
> 若 LLM 與 OCR 使用不同供應商、需要不同金鑰，於 `agents.yaml` 中以 `${oc.env:VAR_NAME, ''}` 引用各自的環境變數，並在 `.env.example` 補上對應項目。

`agents.yaml` 中的 model 區塊範例（實際 base_url 與模型名由使用者填入）：

```yaml
models:
  extract-llm: &model_extract
    client:
      api_key: ${oc.env:OPENAI_API_KEY, ''}
      base_url: <LLM endpoint>
    model: <LLM 模型名>

  glm-ocr: &model_ocr
    client:
      api_key: ${oc.env:OCR_API_KEY, ''}
      base_url: <OCR endpoint>
    model: <調參後的 GLM-OCR 模型名>
```

需宣告三個 agent：

| Agent | 模型 | 用途 | 使用時機 |
|-------|------|------|----------|
| `OCRAgent` | GLM-OCR 變體 | 影像轉文字 | `two_stage` 模式 |
| `ExtractAgent` | Gemma 4 | 文字 → 結構化卡片 | 兩種模式皆用（`two_stage` 的第二步、純文字輸入） |
| `VisionExtractAgent` | Gemma 4 | 影像 → 結構化卡片 | `vision_direct` 模式 |

`ExtractAgent` 與 `VisionExtractAgent` 共用同一個 `output_schema`，僅 prompt 與輸入型態不同。

> OCR 模型名為使用者調整過參數的 GLM-OCR 變體，**不預設任何值**，由使用者填入。

#### ComfyUI 連線與生成參數

```env
COMFYUI_BASE_URL=http://127.0.0.1:8188
COMFYUI_WORKFLOW_PATH=workflows/card_image.json
COMFYUI_POLL_INTERVAL=2.0
COMFYUI_TIMEOUT=300
COMFYUI_BATCH_SIZE=4
COMFYUI_IMAGE_WIDTH=1024
COMFYUI_IMAGE_HEIGHT=576
COMFYUI_NEGATIVE_PROMPT=text, watermark, signature, letters, words, caption, subtitle
```

> 負向 prompt 預設值刻意包含各種文字相關詞彙——記憶錨點圖的設計要求是**不含任何文字**。

#### ComfyUI 節點注入點

每個注入點拆為「節點 ID」與「欄位名」兩個變數，讓程式對應任何 workflow 結構。

```env
COMFYUI_POSITIVE_NODE_ID=6
COMFYUI_POSITIVE_FIELD=text
COMFYUI_NEGATIVE_NODE_ID=7
COMFYUI_NEGATIVE_FIELD=text
COMFYUI_SEED_NODE_ID=3
COMFYUI_SEED_FIELD=seed
COMFYUI_LATENT_NODE_ID=5
COMFYUI_WIDTH_FIELD=width
COMFYUI_HEIGHT_FIELD=height
COMFYUI_OUTPUT_NODE_ID=9
```

| 注入點 | 必要性 |
|--------|--------|
| positive / negative prompt | ✅ 必填 |
| output node | ✅ 必填 |
| seed / latent 尺寸 | ⬜ 選填，未指定則不注入 |

**如何取得節點 ID**：ComfyUI 中以 `Save (API Format)` 匯出，JSON 頂層 key 即為節點 ID。

#### VOXCPM2

**不是 HTTP 服務**：`voxcpm` 是安裝在本機的 Python 套件，模型權重在本機路徑，
推論直接跑在本專案的行程內（GPU）。因此**沒有** endpoint、沒有連線逾時，
也沒有 `language` / `speed` / `speaker_id` 這類參數——語言由文字本身決定，
音色由參考音檔決定。

```env
# 模型權重目錄（含 config.json、model.safetensors、audiovae.pth）
VOXCPM2_MODEL_PATH=/home/jason/disk2/voxcpm2
# 留空為自動選擇（優先 CUDA）
VOXCPM2_DEVICE=

# ── 音色（voice cloning）──
# 參考音檔。留空則每次生成都是隨機音色，整套牌組的聲音不會一致。
VOXCPM2_REFERENCE_WAV=
# 進階：continuation 模式。兩者必須同時給或同時留空。
VOXCPM2_PROMPT_WAV=
VOXCPM2_PROMPT_TEXT=

# ── 生成參數 ──
VOXCPM2_CFG_VALUE=2.0
VOXCPM2_INFERENCE_TIMESTEPS=10
VOXCPM2_NORMALIZE=false
# 對參考音檔降噪。需 ModelScope 的 zipenhancer 模型，會觸發下載，預設關閉
VOXCPM2_ENABLE_DENOISER=false
VOXCPM2_DENOISE=false
# torch.compile 最佳化；除錯時可關閉
VOXCPM2_OPTIMIZE=true

# 本行程內的 GPU 推論本就序列化，設 1 以外的值不會更快
VOXCPM2_CONCURRENCY=1
```

##### 兩種音色來源

`voxcpm` 提供兩條獨立的音色路徑，可單用也可併用：

| 設定 | 對應參數 | 說明 |
|------|----------|------|
| `VOXCPM2_REFERENCE_WAV` | `reference_wav_path` | voice cloning，以 ref_audio token 隔離。**不需要逐字稿** |
| `VOXCPM2_PROMPT_WAV` + `VOXCPM2_PROMPT_TEXT` | `prompt_wav_path` + `prompt_text` | continuation 模式，**必須成對**提供，缺一方套件會直接拋錯 |

三者皆留空時使用隨機音色。**同一套牌組若要音色一致，`VOXCPM2_REFERENCE_WAV` 必填**——
留空時每張卡都是不同的聲音。

`config.py` 須在載入時驗證 `VOXCPM2_PROMPT_WAV` 與 `VOXCPM2_PROMPT_TEXT` 的成對性；
檔案是否存在屬階段性驗證，留給 `audio` 階段檢查。

#### 輸入模式

```env
# two_stage（預設）：影像先經 GLM-OCR 轉文字，再由 Gemma 4 抽取
# vision_direct：影像直接送 Gemma 4 讀取並抽取，跳過 OCR 階段
INGEST_MODE=two_stage
```

> 僅影響影像與 PDF 輸入。純文字輸入一律直接進階段 ②，不受此設定影響。
> 取捨與前提見〈核心機制：雙路徑輸入模式〉。

#### 工作目錄

```env
WORK_DIR=./work
OUTPUT_DIR=./output
```

---

## CLI 介面

| 子命令 | 階段 | 說明 |
|--------|------|------|
| `ocr` | ① | 影像／PDF 轉文字 |
| `extract` | ② | LLM 抽取整理與 prompt 生成 |
| `image` | ③ | ComfyUI 批量圖生成 |
| `audio` | ④ | VOXCPM2 語音生成 |
| `pack` | ⑤ | 路徑整合與打包 |
| `run-all` | ①–⑤ | 全流程串接 |
| `status` | — | 各階段狀態統計 |
| `serve` | — | 啟動本地 Web UI |

### 共用參數

| 參數 | 預設 | 說明 |
|------|------|------|
| `--work` | `$WORK_DIR/cards.csv` | 中間 CSV 路徑 |
| `--force` | false | 忽略 `done`，強制全部重跑 |
| `--only-failed` | false | 只處理 `failed` |

### 輸出 ZIP 結構

```
deck.zip
├── cards.csv
└── media/
    ├── img/{card_id}.png
    └── audio/{card_id}_front.wav
              {card_id}_back.wav
```

> **音檔格式（待確認）**：`voxcpm` 回傳的是 float32 波形陣列，需自行寫檔。
> `soundfile`（voxcpm 既有相依）可直接寫 WAV，**零新增相依**；要輸出 mp3 則需另引入
> 編碼器（ffmpeg／lameenc）。暫定 `.wav`，若記憶引擎必須吃 mp3 再回頭討論。

---

## 測試要點

> 測試方式與 mock 規範以 [testing-strategy.md](testing-strategy.md) 為準。本節只補充本專案獨有的分類與規則。

### 模組分類

| 模組 | 分類 | 執行者 |
|------|------|--------|
| `config.py` `schemas/` `state/` `cli.py` `stages/pack.py` `web/server.py` | 非付費 | AI 執行至通過 |
| `clients/llm_client.py` `clients/ocr_client.py` | **付費或本地模型** | AI 撰寫 mock 骨架，**不執行**，人工驗證 |
| `clients/comfyui_client.py` `clients/tts_client.py` | 本地模型服務 | AI 執行至通過（mock 掉推理呼叫） |
| `stages/ocr` `extract` `image` `audio` | 混合 | 純邏輯可執行；外部呼叫 mock |
| `web/ui.py` | 前端 | 人工驗證 |

**`llm_client.py` 與 `ocr_client.py` 不執行的理由**：兩者走同一套 `agent_factory`，`agents.yaml` 的 endpoint 可指向**線上付費服務或本地模型**，且執行時不易區分。依 [testing-strategy.md](testing-strategy.md)「有免費額度但超過會計費 → 視為付費」與 [llm-integration.md](llm-integration.md)「本地模型一律 mock 掉模型載入與推理呼叫」，兩條規則都導向同一結論：撰寫 mock 骨架但不執行。

> 本專案的 OCR 實際使用調整過參數的 GLM-OCR 變體（本地模型），即使無雲端費用，**仍不在自動測試中真正載入模型**——載入成本高、佔用 VRAM，且會與 ComfyUI 搶資源。

### 專案獨有規則

1. **client 測試不得依賴外部服務啟動**。`comfyui_client` 與 `tts_client` 雖為本地服務，測試仍以 mock HTTP 回應驗證節點注入邏輯、輪詢、逾時與錯誤轉換，確保**無任何服務啟動、無 GPU** 的環境下 pytest 可通過。這同時符合 llm-integration.md 對本地模型「不真正載入」的要求。

2. **`stages/` 測試須拆分**：狀態流轉、篩選、批次切分、檔名組合、唯一性檢查等純邏輯撰寫**可執行**測試；外部呼叫部分 mock。`stages/extract.py` 因 mock 對象為 LLM，該部分測試撰寫後**不執行**。

3. **狀態機測試須涵蓋原子寫入**：以模擬中斷驗證工作檔不損毀。

4. **需人工執行的測試**在改動日誌中必須點名：
   ```
   tests/clients/test_llm_client.py
   tests/clients/test_ocr_client.py
   tests/stages/test_extract.py（LLM 呼叫相關案例）
   ```
