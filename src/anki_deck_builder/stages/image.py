"""階段 ③：聯想圖生成（流程層）。

讀每張卡的 `image_prompt`，交給 ComfyUI 生成一張無文字的視覺記憶錨點，
存進 `media/img/{card_id}.png`，把**相對路徑**寫回 `image_front`。

## 相對路徑寫在 CSV，實體檔案放在 CSV 隔壁

`pack` 以「中間 CSV 所在目錄」為 `media_root` 還原實體路徑（見 `stages/pack.py`），
因此本階段的落腳處也必須是 CSV 隔壁，而不是 `settings.paths.work_dir`——
`--work` 指到別處時兩者並不相同。落腳處於 `run()` 從 store 取得，
不依賴 CLI 傳對參數。

## seed 由 `card_id` 決定

同一張卡重生會得到同一張圖，中斷續作、部分重跑都不會讓牌組的視覺風格跳動。
代價是「不滿意所以重生」拿到的還是同一張——要換圖得改 `image_prompt`
（改了 prompt 不影響 seed，但 prompt 變了圖自然就變了）。

## 併發是「同時在飛的 prompt 數」

`COMFYUI_BATCH_SIZE` 控制的是同時送幾份 workflow 進 ComfyUI 佇列，
**不是** `EmptyLatentImage` 的 `batch_size`（一次生幾張）。ComfyUI 佇列本身
序列化執行，併發只影響排隊深度——排得深一點可以讓 GPU 不必等下一次提交。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from ..clients.protocols import ImageGenClientProtocol
from ..config import Settings
from ..exceptions import StageProcessingError
from ..schemas import CardRow
from ..state import select_pending
from .base import BaseStage, register_stage
from .progress import ProgressReporter

#: 進度條上顯示的階段名稱
PROGRESS_LABEL = "生成聯想圖"

#: 圖片在工作目錄與 ZIP 內的共同相對位置（見 `stages/pack.py` 的 `MEDIA_DIRS`）
IMAGE_SUBDIR = "media/img"

#: seed 取雜湊的前幾個 byte。KSampler 的 seed 上限是 2^64-1，8 bytes 剛好填滿
SEED_BYTES = 8


def stable_seed(card_id: str) -> int:
    """由 `card_id` 導出固定的 seed。

    同一張卡永遠得到同一個值，因此重生的結果可重現。
    """
    digest = hashlib.sha256(card_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:SEED_BYTES], "big")


@register_stage("image")
class ImageStage(BaseStage):
    """把 `image_prompt` 變成 `media/img/{card_id}.png`。"""

    def __init__(
        self,
        client: ImageGenClientProtocol,
        settings: Settings | None = None,
        media_root: str | Path | None = None,
        progress_stream: TextIO | None = None,
    ) -> None:
        """
        Args:
            client: 圖生成 client（依 Protocol 注入，測試以假實作替換）。
            settings: 併發上限的來源。`None` 時併發為 1。
            media_root: 媒體檔的根目錄。預設於 `run()` 取中間 CSV 所在目錄，
                與 `pack` 的 `media_root` 一致。
            progress_stream: 進度輸出目的地，預設 `sys.stderr`。
        """
        self.client = client
        self.settings = settings
        self._media_root = Path(media_root) if media_root is not None else None
        self._progress_stream = progress_stream
        self._progress: ProgressReporter | None = None
        self.concurrency = settings.comfyui.batch_size if settings else 1
        # 單張數十秒，每列寫回一次的成本可忽略；換來的是中斷後不必重生已完成的圖
        self.checkpoint_every = 1

    def validate_settings(self) -> None:
        """階段開始前確認 workflow 與注入點。

        一批圖可能跑半小時，設定錯誤要在第一張送出前就擋下，而不是夾在進度條中間。

        Raises:
            ConfigurationError: workflow 讀不到，或注入點對不上。
        """
        self.client.validate()

    async def run(
        self,
        store,  # noqa: ANN001 - 型別同 BaseStage.run
        force: bool = False,
        only_failed: bool = False,
    ):
        """記下媒體根目錄、備好進度條，再交給骨架執行。

        `process_row()` 拿不到 store，而落腳處必須與 `pack` 的 `media_root`
        一致（中間 CSV 所在目錄），所以在這裡取。

        進度條的總數同理：骨架在 `run()` 內部才算出待處理列，不會往外傳。
        這裡自己再篩一次——多讀一次 CSV，換取**不必改動 `BaseStage`**
        （見 `base.py`：新增階段時加檔案、不改骨架）。
        """
        if self._media_root is None:
            self._media_root = Path(store.path).parent

        rows = await store.read() if store.exists() else []
        targets = select_pending(rows, self.name, force=force, only_failed=only_failed)
        self._progress = ProgressReporter(
            PROGRESS_LABEL, len(targets), stream=self._progress_stream
        )
        self._progress.start()
        try:
            return await super().run(store, force=force, only_failed=only_failed)
        finally:
            self._progress.finish()

    async def process_row(self, row: CardRow) -> Sequence[CardRow]:
        """生成單張圖並回填 `image_front`。

        失敗時直接拋例外，由骨架寫入 `image_error`、標 `failed` 並繼續下一列。
        進度以 `try/finally` 前進——失敗的列也已經處理完了，不前進會讓進度卡住。
        """
        try:
            return await self._generate_for(row)
        finally:
            if self._progress is not None:
                self._progress.advance()

    async def _generate_for(self, row: CardRow) -> Sequence[CardRow]:
        if not row.card_id:
            # 這是 ocr／extract 留下的來源列，不是卡片。它的 image_status 從未被
            # 動過因而是 pending，會被選進本階段，但它沒有 image_prompt 也不該有圖。
            # 同 `ocr.py` 對卡片列的防護，方向相反。
            return ()

        prompt = row.image_prompt.strip()
        if not prompt:
            raise StageProcessingError(
                f"{row.card_id} 沒有 image_prompt，無法生成聯想圖"
                "（該欄由階段 ② extract 填寫）"
            )

        png = await self.client.generate(prompt, seed=stable_seed(row.card_id))
        relative = f"{IMAGE_SUBDIR}/{row.card_id}.png"
        await self._write(self._resolve_media_root() / relative, png)
        row.image_front = relative
        return ()

    def _resolve_media_root(self) -> Path:
        if self._media_root is not None:
            return self._media_root
        if self.settings is not None:
            return self.settings.paths.work_dir
        return Path("./work")

    @staticmethod
    async def _write(path: Path, content: bytes) -> None:
        """寫檔。建立目錄與寫入都是同步 I/O，包進執行緒避免卡住 event loop。"""

        def write_sync() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        await asyncio.to_thread(write_sync)
