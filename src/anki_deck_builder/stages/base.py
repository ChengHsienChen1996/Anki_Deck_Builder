"""階段共用骨架與 registry（流程層）。

所有階段共通的流程只在這裡寫一次：

    讀取中間 CSV → 篩選待處理列 → 逐列處理 → 更新狀態 → 原子寫回

各階段子類只需實作「單列如何處理」（`process_row`）與「本階段需要哪些設定」
（`validate_settings`）。新增階段時**加檔案、不改本檔**——骨架邏輯要動，
先停下來與使用者討論。

三個設計重點：

1. **失敗記錄後跳過**（約束 3）：單列例外一律在此捕捉、寫入該列的 `*_error`、
   狀態設 `failed`，繼續下一列。子類不需要自己 try／except。
2. **一對多**：`process_row` 回傳「這一列產生的新列」。`extract` 一列 `raw_text`
   會切出多張卡，新列 append 在後面，原 `raw_text` 列標 `done` 後保留供追溯
   （`card_id` 為空即代表它不是卡片，`pack` 會略過）。其餘階段回空清單，
   代表只就地更新自己。
3. **定期 checkpoint**：圖生成一批可能跑 30 分鐘，只在最後才寫檔的話，中途
   Ctrl-C 會讓已完成的工作全部白做，違反「可中斷續作」的目標。每完成
   `checkpoint_every` 列就原子寫回一次，且無論成功、失敗或被取消都會在
   離開前再寫一次。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from ..schemas import CardRow
from ..state import CardStore, mark_done, mark_failed, select_pending
from ..state.selector import STAGE_FIELDS

#: 錯誤訊息寫進 CSV 前的長度上限，避免單列失敗把工作檔撐爆
MAX_ERROR_LENGTH = 500


@dataclass(frozen=True)
class StageResult:
    """單次階段執行的結果統計。"""

    stage: str
    processed: int
    succeeded: int
    failed: int
    added: int

    @property
    def all_succeeded(self) -> bool:
        return self.failed == 0


class BaseStage(ABC):
    """階段基底類別。

    子類以 `@register_stage("<階段名>")` 註冊，階段名必須是 `STAGE_FIELDS` 中的鍵，
    骨架才知道該更新哪一組狀態／錯誤欄位。

    註：`pack` 不是 `BaseStage`——它沒有逐列狀態，是走完全部階段後的終端操作。
    """

    #: 由 `register_stage` 填入
    name: ClassVar[str] = ""
    #: 併發上限，由各階段依外部服務的承受度指定；子類可於 __init__ 依設定覆寫
    concurrency: int = 1
    #: 每完成幾列就原子寫回一次；設為 0 表示只在最後寫一次
    checkpoint_every: int = 10

    @abstractmethod
    async def process_row(self, row: CardRow) -> Sequence[CardRow]:
        """處理單一列。

        就地修改 `row` 的內容欄位即可，**不要**自己動狀態欄位或捕捉例外——
        狀態流轉與失敗處理由骨架統一負責。

        Returns:
            這一列產生的**新增**列；沒有新增時回空序列。
        """

    def validate_settings(self) -> None:
        """本階段需要哪些設定，在此檢查（階段性驗證）。

        預設不檢查任何東西。`image` 階段會在此確認 workflow 檔案存在，
        避免那項檢查放在 `config.py` 載入時擋住其他階段。
        """
        return None

    async def run(
        self,
        store: CardStore,
        force: bool = False,
        only_failed: bool = False,
        card_ids: Collection[str] | None = None,
    ) -> StageResult:
        """執行本階段。

        Args:
            card_ids: 只處理這些 `card_id`（Web UI 的單列重跑）。`None` 為不限制，
                行為與本參數存在之前完全相同。

        Raises:
            ValueError: `force` 與 `only_failed` 同時指定。
            WorkFileError: 中間 CSV 讀寫失敗。
        """
        self.validate_settings()

        rows = await store.read()
        targets = select_pending(
            rows, self.name, force=force, only_failed=only_failed, card_ids=card_ids
        )

        state = _RunState(rows=rows)
        if not targets:
            return state.to_result(self.name)

        semaphore = asyncio.Semaphore(max(1, self.concurrency))
        try:
            await asyncio.gather(
                *(self._process_one(row, state, semaphore, store) for row in targets)
            )
        finally:
            await state.flush(store)

        return state.to_result(self.name)

    async def _process_one(
        self,
        row: CardRow,
        state: _RunState,
        semaphore: asyncio.Semaphore,
        store: CardStore,
    ) -> None:
        async with semaphore:
            try:
                created = await self.process_row(row)
            except Exception as exc:  # noqa: BLE001 - 約束 3：任何失敗都只影響該列
                mark_failed(row, self.name, _describe(exc))
                await state.record(
                    failed=True,
                    created=(),
                    store=store,
                    every=self.checkpoint_every,
                )
            else:
                mark_done(row, self.name)
                await state.record(
                    failed=False,
                    created=created,
                    store=store,
                    every=self.checkpoint_every,
                )


@dataclass
class _RunState:
    """一次執行期間的共享狀態，寫檔一律經由 `lock` 序列化。"""

    rows: list[CardRow]
    added: list[CardRow] = field(default_factory=list)
    succeeded: int = 0
    failed: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def processed(self) -> int:
        return self.succeeded + self.failed

    async def record(
        self,
        failed: bool,
        created: Sequence[CardRow],
        store: CardStore,
        every: int,
    ) -> None:
        async with self.lock:
            if failed:
                self.failed += 1
            else:
                self.succeeded += 1
                self.added.extend(created)
            if every and self.processed % every == 0:
                await self._write(store)

    async def flush(self, store: CardStore) -> None:
        async with self.lock:
            await self._write(store)

    async def _write(self, store: CardStore) -> None:
        await store.write([*self.rows, *self.added])

    def to_result(self, stage: str) -> StageResult:
        return StageResult(
            stage=stage,
            processed=self.processed,
            succeeded=self.succeeded,
            failed=self.failed,
            added=len(self.added),
        )


_REGISTRY: dict[str, type[BaseStage]] = {}


def register_stage(name: str):
    """把階段類別註冊到 registry，供 `cli.py` 依名稱查表 dispatch。

    Raises:
        KeyError: 階段名不在 `STAGE_FIELDS` 中（骨架無從得知該更新哪個狀態欄位）。
        ValueError: 同一個名稱重複註冊。
    """

    def decorator(cls: type[BaseStage]) -> type[BaseStage]:
        if name not in STAGE_FIELDS:
            raise KeyError(
                f"未知的階段名稱 {name!r}，合法值：{', '.join(STAGE_FIELDS)}"
            )
        if name in _REGISTRY:
            raise ValueError(f"階段 {name!r} 已由 {_REGISTRY[name].__name__} 註冊")
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return decorator


def get_stage(name: str) -> type[BaseStage]:
    """依名稱取得階段類別。

    Raises:
        KeyError: 該階段尚未實作（後續 phase 才加入）。
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"階段 {name!r} 尚未實作，目前可用：{', '.join(sorted(_REGISTRY)) or '（無）'}"
        ) from None


def registered_stages() -> dict[str, type[BaseStage]]:
    """目前已註冊的階段（複本，改動不影響 registry）。"""
    return dict(_REGISTRY)


def _describe(exc: BaseException) -> str:
    """把例外轉成適合寫入 CSV 單一欄位的訊息。"""
    message = f"{type(exc).__name__}: {exc}".replace("\r\n", "\\n").replace("\n", "\\n")
    if len(message) > MAX_ERROR_LENGTH:
        message = message[: MAX_ERROR_LENGTH - 1] + "…"
    return message
