# tests/fixtures — 對齊樣板

供 Task 1.8（抽取 prompt 設計）與 Task 1.9 / 2.5（extract 階段測試）使用的黃金樣本。
prompt 的品質以「餵入 `ocr_raw/` 能否產出接近 `expected_cards.csv` 的結果」為判準。

## 內容

```
fixtures/
├── pages/               # 原始書頁照片（4 張，JLPT N2 單字書）
│   ├── page_01.jpg      # そう〜そしつ（増大、装置、相当、属する、測定、組織…）
│   ├── page_02.jpg      # そせん〜そのまま（祖先、注ぐ、率直、粗末、備える…）
│   ├── page_03.jpg      # それでも〜尊重（それなり、逸れる、損害、存在…）
│   └── page_04.jpg      # た行 p.335（損得、体育、体温、大会、対角線…）
├── ocr_raw/             # 各頁對應的 OCR 文字（extract 階段的輸入）
│   └── page_01.txt … page_04.txt
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
