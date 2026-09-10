"""版面區塊偵測（服務層，Phase 9）。

`DocLayout-YOLO` 的薄適配層。**選配相依**（`uv sync --all-extras`）——
`OCR_CHUNK_ENABLED=false` 時整條流程照常運作，退回整頁送 OCR。

## 回傳的框不是裁切區域

直接拿偵測框當裁切區，沒偵測到的文字會永久遺失——實測單字書頁 16 個條目
只有 14 個拿到本文框。切法由 `stages/ocr_chunking.py` 依這些框推導，
切出來的塊必然覆蓋全頁（見該模組的模組 docstring）。

## 為什麼要轉正

偵測器對頁面方向極度敏感：實測單字書頁原圖直送偵測到 **0 塊**、轉正後 **28 塊**。
判錯的代價不只是切線位置——它會讓欄序反轉，串接出來的文字順序全錯。

`OCR_CHUNK_PAGE_ROTATION` **預設 `none`**（2026-09-10 起），自動判斷改為 opt-in。
`auto` 的評分已於同日換成「文字框數」，理由與實測數據見 `_best_rotation`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import OCRChunkSettings
from ..exceptions import ConfigurationError

#: 只有這些類別算「文字」，與 `stages/ocr_chunking.TEXT_CLASSES` 一致。
#: 這裡再過濾一次是為了讓 `auto` 的轉正評分只看文字框
_TEXT_CLASSES = frozenset({"plain text", "title", "figure_caption", "table", "table_caption"})

#: 轉正方向 → PIL 的旋轉角度（逆時針為正）
_ROTATIONS: dict[str, int] = {"none": 0, "cw": -90, "ccw": 90, "180": 180}

#: 兩個方向的文字框數差距在這個比例以內時，視為「框數說明不了什麼」，改比面積。
#: 實測的兩群是 33–67 與 0–8，差距遠大於此，所以這條路徑在單字書上不會走到——
#: 它存在是為了兩個方向都偵測健康的版面（例如接近正方形的頁面）
_COUNT_TIE_RATIO = 0.5


def _beats(candidate: tuple[int, float], champion: tuple[int, float]) -> bool:
    """候選的 `(文字框數, 聯集面積比)` 是否勝過目前最佳。

    框數差距顯著時只看框數；接近時改看面積。理由見 `_best_rotation` 的 docstring。
    """
    lo, hi = sorted((candidate[0], champion[0]))
    if hi > 0 and lo / hi < _COUNT_TIE_RATIO:
        return candidate[0] > champion[0]
    return candidate[1] > champion[1]


class LayoutDetector:
    """依 Protocol 提供 `detect()`。模型延後載入，只在第一次偵測時建構。"""

    def __init__(self, settings: OCRChunkSettings) -> None:
        self._settings = settings
        self._model: Any | None = None

    def detect(self, image_path: str | Path) -> tuple[list[dict], tuple[int, int]]:
        """偵測一張影像，回傳 `(框清單, 轉正後的尺寸)`。"""
        from PIL import Image

        base = Image.open(image_path).convert("RGB")
        if self._settings.page_rotation == "auto":
            image, boxes = self._best_rotation(base)
        else:
            angle = _ROTATIONS[self._settings.page_rotation]
            image = base.rotate(angle, expand=True) if angle else base
            boxes = self._run(image)
        return boxes, image.size

    # ── 內部 ─────────────────────────────────────────────────────

    def _best_rotation(self, base: Any) -> tuple[Any, list[dict]]:
        """`auto`：試 0° 與 90°cw，取**文字框數較多**的；框數接近時才比聯集面積。

        ## 為什麼是框數而不是面積（2026-09-10 實測改的）

        原本只比聯集面積，而那個訊號**系統性地偏向偵測失敗的那一邊**：
        方向不對時偵測器不是回傳零個框，而是吐一個低信心的兜底大框
        （實測 p35 轉 cw 後只剩 3 個框，其中 `table` conf=0.117 佔全頁 53%），
        於是「失敗」的 0.537 贏過「成功」那 54 個貼合小框的 0.428。

        41 頁全掃的分佈，框數分得開而面積分不開：

        | | 文字框數 | 聯集面積 |
        |---|---|---|
        | 方向正確 | **33 – 67** | 0.373 – 0.611 |
        | 方向錯誤 | **0 – 8** | 0.000 – 0.741 |

        舊評分在這批 41 頁只對了 13 頁（**32%**，不是 docstring 原本寫的 60%），
        錯的 28 頁全部被切成穿過文字行的直條，抽取階段再把碎片補成完整的卡片
        ——那 28 頁的卡片密度是正確頁的 1.5 倍（20.8 vs 13.8 張/頁），
        80 張中文拼音卡也全部落在這 28 頁裡。

        框數接近時退回比面積：**框數是「偵測健不健康」的代理，不是「方向對不對」的**。
        兩個方向都偵到幾十個框的頁面，框數說明不了什麼，只剩面積可比。

        ## 實測否決的兩道額外防線（不要再試）

        - **「切線切穿文字框的比例」**：壞計畫算出來是 100%，但那是拿**僅有的 1 個框**
          算的，沒有解析度；而正確計畫在 p8 是 52%、p40 是 32%
          （`split_to_budget` 的「寧可切在不理想的位置」本來就會切穿）。分不開。
        - **塊形（最窄塊短邊 ÷ 頁面短邊、最大長寬比）**：正確計畫 0.06–0.28／1.7–7.6，
          錯誤計畫 0.14–1.00／1.6–8.8，區間重疊。p1 的正確計畫比多數錯誤計畫還扁。

        兩者都試過、都不能當門檻。**真正的鑑別訊號只有偵測品質本身**，
        也就是這個函式選的東西。
        """
        from ..stages.ocr_chunking import union_area_ratio

        best: tuple[tuple[int, float], Any, list[dict]] | None = None
        for angle in (0, -90):
            image = base.rotate(angle, expand=True) if angle else base
            boxes = self._run(image)
            text = [tuple(b["box"]) for b in boxes if b["cls"] in _TEXT_CLASSES]
            score = (len(text), union_area_ratio(text, image.size))
            if best is None or _beats(score, best[0]):
                best = (score, image, boxes)
        assert best is not None
        return best[1], best[2]

    def _run(self, image: Any) -> list[dict]:
        model = self._ensure_model()
        settings = self._settings
        det = model.predict(
            image, imgsz=settings.imgsz, conf=settings.conf, verbose=False
        )[0]
        return [
            {
                "cls": det.names[int(b.cls)],
                "conf": round(float(b.conf), 3),
                "box": [round(float(v)) for v in b.xyxy[0]],
            }
            for b in det.boxes
        ]

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from doclayout_yolo import YOLOv10
            from huggingface_hub import hf_hub_download
        except ImportError as error:
            raise ConfigurationError(
                "OCR_CHUNK_ENABLED=true 需要選配相依，請執行 "
                f"`uv sync --all-extras`（原因：{error}）"
            ) from error

        try:
            path = hf_hub_download(
                repo_id=self._settings.model_repo, filename=self._settings.model_file
            )
        except Exception as error:  # noqa: BLE001 - hub 的例外型別未公開
            raise ConfigurationError(
                f"版面偵測模型取不到（{self._settings.model_repo}）：{error}"
            ) from error

        self._model = YOLOv10(path)
        return self._model
