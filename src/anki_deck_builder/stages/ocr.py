"""階段 ①：OCR（流程層）。

輸入端的起點。與其他階段不同，本階段**先建立列、再處理列**：

    prepare()  判別輸入 → PDF 轉圖 → 為每個來源項目建一列（pending）
    run()      逐列 OCR（骨架負責狀態流轉與失敗跳過）

拆成兩步是因為 `BaseStage.run()` 的前提是「CSV 裡已經有列」，而 OCR 是唯一
從外部路徑產生列的階段。中間 CSV 不存在時由 `prepare()` 建立。

## 每列的來源影像記在 `source`

`process_row()` 需要知道自己要辨識哪張圖，而 `CardRow` 沒有專門的欄位。這裡把
影像路徑寫進來源列的 `source`：

- 語義相符——對來源列而言，「來源」就是這張影像
- 不污染卡片——`extract._build_card_row()` 的 `source` 取自 CLI `--source`，
  不從來源列複製
- **可續作**——路徑存在 CSV 裡，`--only-failed` 重跑不必再給 `--input`

## 純文字繞過

`TEXT` 輸入在 `prepare()` 階段就把內容寫進 `raw_text` 並標 `done`，
`run()` 因此找不到待處理列，**不會發出任何 OCR 請求**。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..clients.image_input import crop_to_b64
from ..clients.ocr_client import encode_image_b64
from ..clients.protocols import OCRClientProtocol
from ..config import Settings
from ..exceptions import StageProcessingError
from ..schemas import CardRow, StageStatus
from ..state import CardStore
from .base import BaseStage, StageResult, register_stage
from .input_source import InputKind, detect_input
from .pdf_render import DEFAULT_DPI, OUTPUT_SUBDIR, render_pdf


@dataclass(frozen=True)
class PrepareResult:
    """`prepare()` 的結果統計。"""

    kind: InputKind
    created: int
    skipped: int

    @property
    def total(self) -> int:
        return self.created + self.skipped


logger = logging.getLogger(__name__)


def _log_chunk_fallback(image_path: str, error: Exception) -> None:
    """分塊失敗只記一則警告——它是最佳化，整頁送仍然是可用的行為。"""
    logger.warning(
        "版面偵測失敗，退回整頁送 OCR：%s（%s: %s）",
        image_path, type(error).__name__, error,
    )


@register_stage("ocr")
class OCRStage(BaseStage):
    """把影像轉成 `raw_text`。純文字輸入不經過這裡的 client。"""

    def __init__(
        self,
        client: OCRClientProtocol,
        settings: Settings | None = None,
        work_dir: str | Path | None = None,
        on_finish: Callable[[], Awaitable[object]] | None = None,
        detector: object | None = None,
    ) -> None:
        """
        Args:
            client: OCR client（依 Protocol 注入，測試以假實作替換）。
            settings: PDF DPI 與輸入模式的來源。`None` 時採各項預設值。
            work_dir: PDF 轉圖的落腳處，預設取自 `settings.paths.work_dir`。
            on_finish: 本階段結束後的收尾動作，用於 VRAM 讓渡（卸載 OCR 模型）。
                以注入而非直接呼叫 `model_unload`，是為了讓本階段與供應商無關——
                `keep_alive` 是 Ollama 專屬手段，接線的判斷留給 CLI（Task 2.6）。
            detector: 版面偵測器（`LayoutDetectorProtocol`）。給了才會分塊；
                `None` 時整頁送，與 Phase 9 之前完全相同。
        """
        self.client = client
        self.settings = settings
        self._work_dir = Path(work_dir) if work_dir is not None else None
        self._detector = detector
        self._on_finish = on_finish
        # 併發固定 1（執行計畫 §Q5）：本地推理是 GPU 序列化，併發只會讓多份影像的
        # KV cache 同時佔 VRAM，在 two_stage 兩模型相加 22.5 GB 的情況下更危險
        self.concurrency = 1

    @property
    def is_vision_direct(self) -> bool:
        """是否走影像直送路徑（跳過 OCR）。"""
        return bool(self.settings and self.settings.ingest.mode == "vision_direct")

    async def run(self, store, force: bool = False, only_failed: bool = False):  # noqa: ANN001, ANN201 - 型別同 BaseStage.run
        """執行本階段，結束後觸發收尾動作（若有注入）。

        `vision_direct` 下**完全不辨識**：列已由 `prepare()` 建好，`raw_text`
        留空、`ocr_status` 維持 `pending` 不動——空的 `raw_text` 就是「這列走
        影像路徑」的標記，交由 `extract` 直接讀圖。此時也不觸發收尾動作，
        因為根本沒有載入 OCR 模型。
        """
        if self.is_vision_direct:
            return StageResult(stage=self.name, processed=0, succeeded=0, failed=0, added=0)

        result = await super().run(store, force=force, only_failed=only_failed)
        if self._on_finish is not None:
            await self._on_finish()
        return result

    # ── 建列 ─────────────────────────────────────────────────────

    async def prepare(self, store: CardStore, input_path: str | Path) -> PrepareResult:
        """判別輸入並為每個來源項目建立一列。

        已經在 CSV 裡的來源（同一個影像路徑）會被略過，重跑 `ocr --input` 不會
        產生重複列；要重新辨識既有列請用 `run(force=True)`。

        Raises:
            StageProcessingError: 輸入類型不支援、PDF 無法渲染。
            WorkFileError: 中間 CSV 讀寫失敗。
        """
        kind, items = detect_input(input_path)
        rows = await store.read() if store.exists() else []

        if kind is InputKind.TEXT:
            new_rows, skipped = self._prepare_text(items, rows)
        else:
            if kind is InputKind.PDF:
                items = await self._render_pdf_pages(items[0])
            new_rows, skipped = self._prepare_images(items, rows)

        if new_rows:
            await store.write([*rows, *new_rows])

        return PrepareResult(kind=kind, created=len(new_rows), skipped=skipped)

    async def _render_pdf_pages(self, pdf_path: str) -> list[str]:
        """PDF 轉圖，回傳依頁序排列的圖片路徑。"""
        work_dir = self._resolve_work_dir()
        dpi = self.settings.ingest.pdf_dpi if self.settings else DEFAULT_DPI
        rendered = await render_pdf(pdf_path, work_dir / OUTPUT_SUBDIR, dpi=dpi)
        return [path for _, path in rendered]

    def _resolve_work_dir(self) -> Path:
        if self._work_dir is not None:
            return self._work_dir
        if self.settings is not None:
            return self.settings.paths.work_dir
        return Path("./work")

    def _prepare_text(
        self, items: Sequence[str], existing: Sequence[CardRow]
    ) -> tuple[list[CardRow], int]:
        """純文字：直接讀進 `raw_text` 並標 `done`，不碰 OCR client。"""
        known = self._known_sources(existing)
        new_rows: list[CardRow] = []
        skipped = 0

        for index, item in enumerate(items, start=1):
            if item in known:
                skipped += 1
                continue
            try:
                text = Path(item).read_text(encoding="utf-8")
            except OSError as error:
                raise StageProcessingError(f"文字檔讀取失敗：{item}（{error}）") from error
            new_rows.append(
                CardRow(
                    source=item,
                    raw_text=text,
                    ocr_source_page=index,
                    ocr_status=StageStatus.DONE,
                )
            )
        return new_rows, skipped

    def _prepare_images(
        self, items: Sequence[str], existing: Sequence[CardRow]
    ) -> tuple[list[CardRow], int]:
        """影像：只建列，內容留給 `process_row()` 填。"""
        known = self._known_sources(existing)
        new_rows: list[CardRow] = []
        skipped = 0

        for index, item in enumerate(items, start=1):
            if item in known:
                skipped += 1
                continue
            new_rows.append(CardRow(source=item, ocr_source_page=index))
        return new_rows, skipped

    @staticmethod
    def _known_sources(rows: Sequence[CardRow]) -> set[str]:
        """已在 CSV 中的來源路徑。只看有頁碼的列——卡片列的 `source` 是書目資訊。"""
        return {row.source for row in rows if row.source and row.ocr_source_page is not None}

    # ── 處理列 ───────────────────────────────────────────────────

    async def process_row(self, row: CardRow) -> Sequence[CardRow]:
        """辨識單張影像，結果寫進 `raw_text`。

        失敗時直接拋例外，由骨架寫入 `ocr_error`、標 `failed` 並繼續下一列。
        """
        if row.card_id:
            # 這是 extract 產出的卡片列。它的 ocr_status 沿用自來源列，在
            # vision_direct 下會是 pending，因而被選進本階段；但它的 source
            # 是書目資訊（--source）而非影像路徑，拿去讀檔必然失敗。
            return ()

        if row.raw_text.strip():
            # 已經有文字就沒有 OCR 可做。涵蓋兩種情形：純文字輸入（prepare 已填好），
            # 以及 Phase 1 留下的舊工作檔——那時沒有 ocr 階段，這些列的 ocr_status
            # 一直是 pending，但它們的 raw_text 早就有內容、也抽取完了。
            return ()

        if not row.source:
            raise StageProcessingError(
                "此列沒有影像來源，無法辨識（來源路徑應由 prepare() 寫入 source 欄位）"
            )

        row.raw_text = (
            await self._recognize_chunked(row.source)
            if self._detector is not None
            else await self.client.recognize(await encode_image_b64(row.source))
        )
        return ()

    async def _recognize_chunked(self, image_path: str) -> str:
        """分塊辨識：偵測 → 推結構 → 完整分割 → 逐塊送 OCR → 依序串接。

        整頁尺度下 OCR 會跳過小字——實測同一頁的 furigana 命中從 1/15 變成 8/15，
        而且分塊還比整頁快三倍（見 `stages/ocr_chunking.py` 的模組 docstring）。

        **偵測失敗一律退回整頁**，不讓分塊變成新的失敗來源：分塊是最佳化，
        整頁送仍然是可用的行為。
        """
        from .ocr_chunking import TEXT_CLASSES, contains_text, looks_degenerate, plan

        settings = self.settings.ocr_chunk if self.settings is not None else None
        budget = settings.budget_px if settings else 4_000_000

        try:
            boxes, size = await asyncio.to_thread(self._detector.detect, image_path)
        except Exception as error:  # noqa: BLE001 - 偵測失敗退回整頁，不中斷本列
            _log_chunk_fallback(image_path, error)
            return await self.client.recognize(await encode_image_b64(image_path))

        _, chunks = plan(boxes, size, budget)
        # 只送有文字框落在裡面的塊。空白塊（書溝、頁緣、留白）跳過——
        # 它們沒有文字可讀，卻會讓 OCR 退化成無限重複
        text_boxes = [tuple(b["box"]) for b in boxes if b["cls"] in TEXT_CLASSES]
        wanted = [c for c in chunks if contains_text(c, text_boxes)]
        if len(wanted) <= 1:
            return await self.client.recognize(await encode_image_b64(image_path))

        texts: list[str] = []
        for index, b64 in enumerate(await asyncio.to_thread(crop_to_b64, image_path, size, wanted)):
            result = str(await self.client.recognize(b64))
            if looks_degenerate(result):
                # 有內容的塊也可能退化。丟掉它比讓垃圾污染整頁好——
                # 缺一塊還看得出來，混進幾百行重複則會讓抽取整個歪掉
                logger.warning(
                    "第 %d 塊的辨識結果是重複退化，已丟棄：%s", index + 1, image_path
                )
                continue
            texts.append(result.strip())
        return "\n".join(t for t in texts if t)
