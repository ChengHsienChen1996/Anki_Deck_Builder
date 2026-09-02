"""版面區塊偵測（服務層，Phase 9）。

`DocLayout-YOLO` 的薄適配層。**選配相依**（`uv sync --extra layout`）——
`OCR_CHUNK_ENABLED=false` 時整條流程照常運作，退回整頁送 OCR。

## 回傳的框不是裁切區域

直接拿偵測框當裁切區，沒偵測到的文字會永久遺失——實測單字書頁 16 個條目
只有 14 個拿到本文框。切法由 `stages/ocr_chunking.py` 依這些框推導，
切出來的塊必然覆蓋全頁（見該模組的模組 docstring）。

## 為什麼要轉正

偵測器對頁面方向極度敏感：實測單字書頁原圖直送偵測到 **0 塊**、轉正後 **28 塊**。
但自動判斷不可靠（24 頁同一本書分成 15 none／9 cw），所以
`OCR_CHUNK_PAGE_ROTATION` 可以手動指定，見 `config.py` 該欄位的說明。
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
        """`auto`：試 0° 與 90°cw，取文字框**聯集**面積較大的。

        **這個判斷不可靠**（實測一致率約 60%），保留只是為了在使用者沒指定時
        有個預設。真的在意順序正確就手動指定 `OCR_CHUNK_PAGE_ROTATION`。
        用聯集而非面積總和，是因為偵測框大量重疊、總和會重複計算。
        """
        from ..stages.ocr_chunking import union_area_ratio

        best: tuple[float, Any, list[dict]] | None = None
        for angle in (0, -90):
            image = base.rotate(angle, expand=True) if angle else base
            boxes = self._run(image)
            text = [tuple(b["box"]) for b in boxes if b["cls"] in _TEXT_CLASSES]
            score = union_area_ratio(text, image.size)
            if best is None or score > best[0]:
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
                f"`uv sync --extra layout`（原因：{error}）"
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
