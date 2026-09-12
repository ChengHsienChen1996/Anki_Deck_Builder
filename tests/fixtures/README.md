# tests/fixtures — 對齊樣板

供 Task 1.8（抽取 prompt 設計）與 Task 1.9 / 2.5（extract 階段測試）使用的黃金樣本。
prompt 的品質以「餵入 `ocr_raw/` 能否產出接近 `expected_cards.csv` 的結果」為判準。

## 內容

```
fixtures/
├── materials/           # 教材形態樣本（Phase 2 驗收後補上，見〈教材形態〉）
│   ├── index_english.txt      # 索引式：只有詞條與詞性，**沒有釋義**
│   ├── english_with_defs.txt  # 語言類、非日文、有釋義
│   └── pharmacology.txt       # 非語言領域，有解釋
├── pages/               # 原始書頁照片（4 張，JLPT N2 單字書）
│   ├── page_01.jpg      # そう〜そしつ（増大、装置、相当、属する、測定、組織…）
│   ├── page_02.jpg      # そせん〜そのまま（祖先、注ぐ、率直、粗末、備える…）
│   ├── page_03.jpg      # それでも〜尊重（それなり、逸れる、損害、存在…）
│   └── page_04.jpg      # た行 p.335（損得、体育、体温、大会、対角線…）
├── ocr_raw/             # 各頁對應的 OCR 文字（extract 階段的輸入）
│   └── page_01.txt … page_04.txt
├── ocr_compare/         # 照片品質 A/B 的重拍樣本（見〈ocr_compare〉）
│   ├── 1/P19.jpg, 1/P22.jpg   # 第一輪：只把對焦拍好
│   └── 2/P19.jpg, 2/P22.jpg   # 第二輪：+1 EV、200MP、後製拉滿對比
├── expected_cards.csv   # 理想抽取結果（extract 階段的期望輸出）
└── README.md
```

## 重要注意

1. **`ocr_raw/` 目前是人工轉寫**，非真實 GLM-OCR 輸出。取得真實輸出後**請直接覆蓋**這四個檔案——版面雜訊、辨識錯誤的真實樣態對 prompt 調校很重要，轉寫版偏乾淨，會高估抽取品質。
2. `expected_cards.csv` 為**代表性子集**（16 張），非四頁全部詞條。刻意涵蓋：
   - 詞性：名詞、五段／一段動詞、する動詞、い形、な形、副詞、接續詞
   - deck 分派邏輯：依詞性歸入 `日語::N2::{動詞|名詞|形容詞|副詞|文法}`
   - 欄位完整度：含 `reading`、`image_prompt`（帶統一風格後綴與 no text 約束）、`tts_front_text`（假名）、`tts_back_text`（純日文例句）
3. 狀態欄位設定為 `ocr_status=done`、`extract_status=done`、其餘 `pending`——即「剛完成抽取」的狀態，可直接作為 image / audio 階段的測試輸入。
4. `pages/` 同時是 **`vision_direct` 模式**（Phase 2 Task 2.5）與**兩模式品質比對**（Phase 2 驗收流程第 8 步）的輸入。
5. 圖片共約 10 MB。若在意 repo 體積，可改放外部儲存並於此處留下取得方式，但 `ocr_raw/` 與 `expected_cards.csv` 必須留在版控內。

## `ocr_compare/`：照片品質 A/B 的重拍樣本

**不被任何自動測試讀取**，是 `scripts/ab-photo-quality.py` 的輸入，也是
「重拍照片救不了 OCR 讀不出小寫假名與濁點」這個結論的證據。留在版控內是為了讓
下一個人能**重跑對照而不必重拍**——舊照片在 `work/cards.csv` 的 `source` 欄位指向
機器上的原始路徑，那個路徑不在版控裡，弄丟就再也對照不了。

兩輪都是 p19 與 p22（全書 `marks` 錯誤最密集的兩頁，共 9 個目標讀音）：

| | 拍攝條件 | 結果 |
|---|---|---|
| `1/` | 相機固定、只把對焦拍好（6–7 MP、偏藍、曝光未改） | 修好 2/9，注音讀出最多的一組 |
| `2/` | 曝光補償 +1 EV、200MP、**後製把對比拉到最高** | 同樣 2/9，但注音反而比舊照片少 |

結論與完整數據見
[.agent/plans/photo-quality-ab.md](../../.agent/plans/photo-quality-ab.md)：
**對焦是唯一有效的變因，後製不要動對比與 levels**（截掉灰階過渡，注音筆畫細，
被吃掉最多）。正解是 `scripts/fix-reading-marks.py`，與照片無關。

## 教材形態（Phase 2 驗收後補上）

`ocr_raw/` 全部是日文詞條頁，而 Phase 2 驗收暴露的多數問題都出在**其他形態的教材**
上——只用一種形態測會高估品質。`materials/` 補上三種，各自隔離一個變因：

| 檔案 | 語言 | 有無釋義 | 領域 | 隔離的變因 |
|------|------|----------|------|------------|
| `ocr_raw/page_01.txt`（既有） | 日文 | 有 | 語言 | 基準 |
| `english_with_defs.txt` | 英文 | 有 | 語言 | **語言** |
| `pharmacology.txt` | 中文 | 有 | **非語言** | **領域** |
| `index_english.txt` | 英文 | **無** | 語言 | **有無釋義** |

驗收實測顯示：**關鍵變因是「有無釋義」，不是語言、也不是領域**。有釋義的教材，
模型做的是「抽取」；索引式教材則是「生成」——憑知識造釋義、猜詞性、編例句，
本質更難且無從校對。詳見
[phase-2-execution-plan.md](../../.agent/plans/phase-2-execution-plan.md) §2.7。

### 各形態專門捕捉的失敗

| 教材 | 曾捕捉到的問題 |
|------|----------------|
| `index_english.txt` | 例句整段缺失（101 張中 31 張空）、deck 退回 `Vocabulary` 泛稱、釋義寫成英文、無聲產出不足（7 條目回 1 張） |
| `pharmacology.txt` | prompt 中的詞性列舉套不上非語言領域，8 張卡全塞進 `藥物類別` 一個桶 |
| `english_with_defs.txt` | 證明上述問題與「語言」無關——同為英文但有釋義時，10／10 全對 |

> `index_english.txt` 的內容為**自行編寫**的常見基礎詞彙（B 開頭，含 `•` 子項與
> 段落標題），形態比照真實索引頁，不取自任何特定書籍。

## 對齊判準（給 prompt 調校時參考）

以 `ocr_raw/` 餵入抽取 prompt 後，逐項比對：

| 檢查項 | 期望 |
|--------|------|
| 詞條切分 | 一個 `□` 詞條對應一張卡，不合併、不遺漏 |
| `reading` | 與書中標音一致（如 ぞくする、そっちょく） |
| `back` | 保留書中多義項，以「；」分隔，不自行增刪語義 |
| `example` | 日文例句 + `\n` + 中文翻譯，取自書中原句 |
| `tts_back_text` | 只含日文例句，無中文 |
| `hint` | 詞性標記（自サ、他下一、な形…） |
| deck 分派 | 依詞性，而非依五十音分組 |
| `image_prompt` | 英文、具場景性、結尾帶統一風格後綴與 no text 約束 |
| 系統欄位 | `created_at` 至 `review_count` 全空 |
