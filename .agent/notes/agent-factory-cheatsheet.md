# agent_factory Cheatsheet（OpenAIAgentsSDKFactory）

> 來源：`src/agent_factory/README.md`（submodule，commit `083cec6`，heads/main）
> 建立日期：2026-08-21。本檔為速查摘要，**完整規格一律以 README 為準**。

---

## 1. 一句話定位

以 `openai-agents` SDK 為底，**YAML 驅動**建立 Agent，並在執行時套用 **TPM／RPM／RPD 三維度速率限制**。
支援任何 OpenAI-compatible endpoint（OpenAI、Gemini、Ollama、自架）。

---

## 2. 本專案「不要重造」的四個輪子

| 能力 | 已由 agent_factory 提供 | 本專案要做的事 |
|------|------------------------|---------------|
| Agent 建立與管理 | `create_agent_factory()` → `get_agent_by_name()` | 寫 `agents.yaml` |
| System prompt 載入 | YAML `instruction_file_path` 指向 .md | 寫 `prompts/*.md` |
| Structured output 解析 | YAML `output_schema` 指向 Pydantic model | 定義 `ExtractOutput` |
| 速率限制／預扣退款 | `LimitAgentRunner.run()` 內建 `limits_guard_multi` | **完全不實作**，只需確保模型有 limits 宣告 |

外加：設定驗證（Pydantic）、多供應商切換（改 YAML 的 `base_url` / `model`）、多模態影像 token 估算。

**不要用 httpx 自己打 endpoint** —— 那會繞過整套速率限制。

---

## 3. 標準呼叫（本專案 `llm_client.py` / `ocr_client.py` 就這幾行）

```python
from agent_factory.core import create_agent_factory
from agent_factory.limit_runner import LimitAgentRunner

factory = create_agent_factory()               # 讀 YAML_SETTINGS_FILE 環境變數
agent   = factory.get_agent_by_name("ExtractAgent")   # 找不到 → KeyError
runner  = LimitAgentRunner(agent=agent)
result  = await runner.run(input_=raw_text)    # input_: str | list[TResponseInputItem]
output  = result.final_output                  # 已依 output_schema 解析為 Pydantic model
```

- `factory` 建立一次後**重用**，不要每次呼叫重建。
- `AgentFactory.create_factory_from_yaml(path)`：不吃環境變數、直接指定 YAML。
- `runner.extract_tool_usage(result) -> ToolTrace`：本專案暫時用不到。

---

## 4. 環境變數（agent_factory 自己定義的全集）

| 變數 | 必填 | 預設 | 說明 |
|------|:----:|------|------|
| `OPENAI_API_KEY` | 是 | — | API 金鑰（或相容供應商的 key） |
| `YAML_SETTINGS_FILE` | 是 | — | agents YAML 路徑 |
| `GLOBAL_CONCURRENCY` | 否 | 6 | 最大並發請求數 |
| `RPM` | 否 | 200 | 全域每分鐘請求上限 |
| `TPM` | 否 | 30000 | 全域每分鐘 token 上限 |

> **endpoint 與模型名不放 `.env`**，寫在 YAML 的 `client.base_url` / `model`。
> 需要多把金鑰時，於 YAML 用 `${oc.env:OCR_API_KEY, ''}` 引用，再補進 `.env.example`。

---

## 5. YAML 結構速記

```yaml
openai: &openai                        # 可用 YAML anchor 共用 client 區塊
  client:
    api_key: ${oc.env:OPENAI_API_KEY, ''}
    base_url: https://api.openai.com/v1
  params:
    temperature: 0.5

models:
  gpt-41: &model_gpt41
    <<: *openai
    model: gpt-4.1

agents:
  default:                             # 分組名稱可自訂（如 multimodal）
    - ExtractAgent:
        name: ExtractAgent             # ← get_agent_by_name() 用的就是這個
        model_instruction:
          dynamic_prompt: false
          instruction_file_path: ${__dir__}/prompts/extract_cards.md
        output_schema: anki_deck_builder.schemas.extract_output.ExtractOutput
        model_params:
          <<: *model_gpt41
          params:
            temperature: 0.3
          limits:
            policy: concurrency_only   # 本地模型建議這樣寫
```

- `${__dir__}` = **YAML 檔所在目錄**（本專案 `agents.yaml` 放根目錄，故 `${__dir__}/prompts/` 正好對上根層 `prompts/`）。
- `${oc.env:VAR, 'default'}` = OmegaConf 環境變數解析。
- `output_schema` 是 **dotted path**，指向 Pydantic `BaseModel`。
- 動態 prompt（本專案 Phase 1 用不到）：`dynamic_prompt: true` + `dynamic_module_path`（PayloadBuilder）+ `model_context_path`。

---

## 6. 多模態影像輸入（Phase 2 會用）

`input_` 傳 message list，影像以 `input_image` + **base64 data URL**：

```python
model_input = [
    {"role": "user", "content": [
        {"type": "input_image", "detail": "auto",
         "image_url": f"data:image/jpeg;base64,{b64}"}]},
    {"role": "user", "content": "Table Recognition:"},
]
result = await runner.run(input_=model_input)
```

- YAML 設定與一般 agent **完全相同**，只要 `model` 具 vision 能力。
- token 估算：base64 字串**絕不**進文字計數，改依寬高套供應商公式（JPEG/PNG/WebP/GIF header 純 Python 解析，不裝 Pillow）。
- **遠端 `http(s)` 影像 URL 一律無法取得尺寸**（刻意不發網路請求）→ 走 high-detail 上限保守高估。要精準就傳 data URL。
- 參考實作：`src/agent_factory/tests/test_multimodal.py`（Ollama + `glm-ocr-optimized:latest`）。

---

## 7. 速率限制（本專案只需「正確宣告」，不需實作）

**等待順序**：Umbrella TPM → 模型 TPM → 模型 RPM → 模型 RPD → 全域 RPM → 全域 Semaphore
呼叫完成後依 `raw_responses` 實際用量**補扣或退款**；供應商完全不回報用量時整筆退回並記一則 error（Ollama 即屬此情況，**屬已知行為非錯誤**）。

### 三種策略

| policy | TPM | RPM／RPD | 全域併發 | 適用 |
|--------|-----|---------|---------|------|
| `enforced` | 管制 | 管制 | 是 | 雲端供應商（**必須填 TPM 與 RPM，否則啟動驗證失敗**） |
| `concurrency_only` | 不管 | 不管 | 是 | 本地／自架模型（GLM-OCR 變體適用） |
| `unlimited` | 不管 | 不管 | 否 | 特殊情境 |

### 設定來源優先序

**YAML `model_params.limits` > `MODEL_LIMITS`（`rate_limiter/limits_parameters.py`）> `DEFAULT_POLICY`**

- `DEFAULT_POLICY` 預設 `concurrency_only`，可由 `LIMIT_DEFAULT_POLICY` 環境變數覆寫；走到這層會發一次 warning，**不拋例外**。
- 同一模型被兩個 agent 以**不同** limits 宣告 → 以先註冊者為準 + warning，不合併。設定相同則冪等。
- `TPD` 只是記錄用，實際管制看 `TPM`；有 `RPD` 才建立每日限制器。

> ⚠️ **與 architecture.md 的認知差異（需向使用者確認）**：architecture.md 寫「模型未登錄 `MODEL_LIMITS` 會拋 `KeyError`」，但 README 明確說明未登錄時會**套 `DEFAULT_POLICY` 並僅發一次警告**。若照 README，本專案在 YAML 直接宣告 `limits` 即可，**不需要改 submodule 也不需要注入 MODEL_LIMITS**。

### 其他

- `enforced` 模型單次預扣量 > 該模型 TPM → `AsyncTokenBucket.acquire()` 立即拋 `ValueError`（避免靜默卡死）。
- 全域 Umbrella 預設 `NoopUmbrella`；要跨模型統一管控才換 `AdaptiveUmbrella`。

---

## 8. API 速查

| 物件 | 方法 | 說明 |
|------|------|------|
| `agent_factory.core` | `create_agent_factory() -> AgentFactory` | 讀 `YAML_SETTINGS_FILE` |
| | `AgentFactory.create_factory_from_yaml(path)` | 指定路徑 |
| | `factory.get_agent_by_name(name) -> Agent` | 查不到拋 `KeyError` |
| | `@AgentFactory.register(name)` | 手動註冊 builder callable |
| `agent_factory.limit_runner` | `LimitAgentRunner(agent=agent)` | — |
| | `await runner.run(input_, context=None, **kwargs) -> RunResult` | 帶速率限制執行 |
| | `await runner.extract_tool_usage(run_result) -> ToolTrace` | 取 tool call items |
| `agent_factory.config_loader` | `AgentConfigLoader.load_raw(path) -> DictConfig` | 注入 `__dir__` |
| | `AgentConfigLoader.load_validated(path) -> list[AgentConfig]` | 失敗拋含 agent name 的 `ValueError` |
| `agent_factory.agent_builder` | `AgentBuilder.build(agent_config) -> Agent` | 靜態／動態 prompt 分支在此 |

---

## 9. 相依與坑

**執行期相依**（submodule `pyproject.toml`）：`openai-agents>=0.3.3`、`omegaconf>=2.3.0`、`aiolimiter>=1.2.1`、`tiktoken>=0.12.0`、`python-dotenv>=1.1.1`。requires-python `>=3.12`。

**已知坑**：

1. **Gemini `AQ.` 前綴 key** 走 OpenAI-compatible endpoint 會 `400 Multiple authentication credentials received` → 改用 Google Cloud Console 的 `AIza` 格式。
2. **遠端 URL 影像**取不到尺寸 → 保守高估。
3. **未涵蓋供應商**的影像走 `FALLBACK_IMAGE_TOKENS`（3000）+ warning。
4. **Ollama 不回報 usage** → 全額退款路徑 + 一則 error log，屬正常。
5. 只有 `gpt-4o-mini` / `gpt-4.1-mini` 的影像估算經真實計費驗證。

**本 repo 的實體結構**（與 README 的邏輯路徑不同，接線時務必注意）：

```
src/agent_factory/                 # submodule 根
├── __init__.py                    # from .src.agent_factory.core import create_agent_factory
│                                  # from .src.agent_factory.limit_runner import LimitAgentRunner
├── pyproject.toml                 # package name: openai-agent-sdk-factory，packages 在 src/
└── src/agent_factory/             # 真正的套件本體（core.py、limit_runner.py…）
```

→ 有**兩層** `agent_factory`。`import agent_factory` 解析到哪一層取決於安裝方式，見 Phase 1 執行計畫〈待確認事項 Q1〉。
