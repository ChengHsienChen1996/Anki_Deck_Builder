# 改動總結 — 聯想圖 prompt 避開會帶出文字的道具

日期：2026-08-27
分支：dev_ai
相關 commit：`e37af64`

> 起因：使用者評估把文生圖模型換成 **FLUX.2-dev**，注意到它沒有可用的負向 prompt。
> 查證後發現「負向失效」不是最大問題，**模型太會寫字**才是；而防文字這件事
> 不論換不換模型都該從 prompt 治本。本次先做治本的部分（選項 A），
> FLUX 的評估留待日後（見〈待決事項〉）。

---

## 變更清單

### 修改 prompt

1. `prompts/image_prompt_template.md`：新增〈避開會帶文字的道具〉。
   附 308 張卡的實測分布與「道具 → 替代畫面」對照表，並說明為什麼這是治本做法
2. `prompts/extract_cards.md`：`image_prompt` 一節新增第 5 條規則與同一張對照表；
   氛圍片語的例子從 `taxonomy chart atmosphere` 換掉（`chart` 會被畫出來）

### 新增

3. `prompts/image_prompt_rewrite.md`：改寫既有 prompt 用的指令
4. `agents.yaml`：`ImagePromptAgent`（純文字輸出，不設 `output_schema`）
5. `scripts/rewrite-image-prompts.py`：**只改 `image_prompt` 一欄**並把
   `image_status` 設回 `pending`。重跑 `extract` 會連 `front`／`back`／`example`
   全部重生，那些欄位是人工檢視過的

## 測試結果

- pytest：680 passed / 0 failed；ruff check 無錯
- `tests/test_prompts.py` 的統一風格後綴檢查照舊通過（後綴一字未改）

## 執行結果（work/cards.csv，308 張卡）

| 輪次 | 候選 | 實際改寫 | 重生耗時 |
|------|------|----------|----------|
| 第一輪 | 57 | 56 | 8 分 52 秒 |
| 第二輪 | 19 | 10 | 約 1.5 分 |
| **合計** | — | **58 張 prompt、58 張圖** | — |

`output/deck.zip` 重新打包：**35.8 MB**。

## 第二輪為什麼存在：偵測清單是實測補出來的

第一輪跑完抽樣看圖，抓到兩張仍在寫字：

- `p2_016`（any）：prompt 裡的 `abstract glowing symbol` 讓書封被寫上 `CALIOVAT`
- `p2_034`（appointment）：`appointment card` 上出現假日文

原因是道具清單漏了 **`card` 與 `symbol`**。補上
`card, symbol, logo, badge, stamp, envelope, menu, map, invoice` 後重跑，
再抽樣九張確認全部乾淨。

**教訓：這種清單不可能一次想全，得靠看圖回饋。** 未來若再發現漏字，
把該名詞補進 `scripts/rewrite-image-prompts.py` 的 `TEXT_PROPS` 與兩份 prompt 的
對照表，再對受影響的卡片跑一次即可。

另修了偵測器兩個誤判：
- `off-screen`（連字號複合詞）被當成「還有螢幕」→ 改用 `(?<![-\w])` 邊界
- `paper` 被當成問題，但「空白紙張」本來就是規則建議的**替代品** → 從清單移除

## 已知殘留

- **偵測器仍標記 16 張**，多為 `closed books`、`blank paper documents` 這類
  已經安全的用法；`p1_071` 的詞條本身就是 **alarm clock**，無從迴避。
  腳本的提示文字因此改成「看一眼是否安全」，不是判決
- **部分改寫語意變弱**：例如 `advertisement` 原本畫海報，改寫後成了抽象光束——
  文字沒了，但「廣告」的語意也淡了。拿掉道具有時會拿掉語意錨點本身。
  個案用 Web UI 的「聯想圖」分頁改 prompt 單張重生最省事

## 待決事項：要不要換 FLUX.2

查證結果（2026-08-27）：

| 項目 | 數字 |
|------|------|
| FLUX.2-dev 參數量 | 32B，rectified flow transformer |
| 文字編碼器 | Mistral Small 3.1（不再是 T5+CLIP） |
| ComfyUI 檔案 | DiT fp8mixed **33.0 GB**；編碼器 bf16 33.1 GB／fp8 16.8 GB／fp4 11.4 GB |
| 官方說法 | 兩者同時載入且不 offload 需 **80 GB 以上** |
| 本機 | RTX 3090 **24 GB**——即使全量化，DiT(fp4 16 GB)+編碼器(11.4 GB)=27.4 GB 仍塞不下 |

**負向的部分**：FLUX.2-dev 一樣是 guidance-distilled（官方範例 `guidance_scale=4`、
28–50 步），走蒸餾路徑沒有真正的 CFG，負向 conditioning 不起作用。

三個選項（按代價排序）：

- **A. 維持 SDXL，只改 prompt** ← **本次已完成**
- **B. 試 FLUX.2-klein-9B-fp8**（官方另有 4B／9B 版，約 9–10 GB，24 GB 卡跑得動）。
  做法照 2026-08-26 換 SDXL 那次：挑 8 張真實 prompt（4 張多元素、4 張含文字道具）
  跑 A/B，只數「多元素齊不齊」與「有沒有出字」
- **C. dev 當單張重生的後備**：批次用 SDXL／klein，個別卡片怎麼調都不對時才切過去。
  Web UI 的單張重生正是為這個場景做的，一張三分鐘可接受

一個**程式面的前置條件**：`clients/workflow.py` 把「負向 prompt」列為**必填**注入點。
FLUX workflow 若沒有負向 CLIPTextEncode，`validate()` 會在第一張送出前擋下。
建議在 workflow 裡留一個接著但不起作用的負向節點（零程式改動），
而不是把 `negative_node_id` 改成選填。

## 備註

- 備份：`work/cards.csv.bak-before-rewrite`（改寫前）與
  `work/cards.csv.bak-2026-08-26`（SDXL 重生前），各 0.2 MB，確認無誤後可刪
- 五個階段仍是 311/311 全 `done`
