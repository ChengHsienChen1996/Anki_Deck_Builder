"""背景階段執行的單一飛行註冊表（介面層）。

階段一跑就是幾分鐘到半小時，HTTP 請求不能等它——所以丟進背景 task，請求端
只拿到「已受理」，進度另外查（`service.stage_progress()`）。

**同時只允許一個階段在跑。** 這不是保守，是硬體事實：GPU 只有一張，image 與
audio 各自要 6–8 GB，兩個並行必然互相搶 VRAM；而且兩者都會寫同一份中間 CSV，
並行等於兩份 checkpoint 互相覆蓋。第二個請求一律回絕，不排隊——排隊會讓使用者
以為按鈕沒反應，還得多一套佇列狀態要維護。

這裡只管「誰在跑、跑完了沒、有沒有拋例外」，**不含任何業務邏輯**：實際要跑什麼
由呼叫端傳進來的 coroutine 決定。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class StageBusyError(RuntimeError):
    """已經有階段在跑了。附上是哪一個，讓使用者知道在等什麼。"""

    def __init__(self, running: str) -> None:
        super().__init__(f"另一個階段（{running}）正在執行，請等它結束再試")
        self.running = running


@dataclass
class TaskState:
    """一次背景執行的狀態快照。"""

    stage: str
    started_at: float
    finished_at: float | None = None
    #: 階段回傳的結果（`StageResult` 的清單，audio 兩側會有兩筆）
    results: list[Any] = field(default_factory=list)
    #: 失敗時的訊息；成功為 `None`
    error: str | None = None

    @property
    def running(self) -> bool:
        return self.finished_at is None

    @property
    def elapsed(self) -> float:
        return (self.finished_at or time.monotonic()) - self.started_at


class StageRunner:
    """單一飛行的背景執行器。

    一個行程一個實例（`server.py` 持有），因此「正在跑什麼」對整個服務是一致的。
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._current: TaskState | None = None
        #: 每個階段最後一次執行的狀態，供 UI 在任務結束後仍查得到結果
        self._history: dict[str, TaskState] = {}

    @property
    def running_stage(self) -> str | None:
        return self._current.stage if self._current is not None else None

    def state_for(self, stage: str) -> TaskState | None:
        """該階段目前或最近一次的執行狀態；從未跑過回 `None`。"""
        if self._current is not None and self._current.stage == stage:
            return self._current
        return self._history.get(stage)

    def start(self, stage: str, work: Callable[[], Awaitable[Any]]) -> TaskState:
        """受理一次背景執行並立刻回傳狀態。

        Raises:
            StageBusyError: 已有階段在跑。
        """
        if self._current is not None:
            raise StageBusyError(self._current.stage)

        state = TaskState(stage=stage, started_at=self._clock())
        self._current = state
        self._task = asyncio.create_task(self._run(state, work))
        return state

    async def _run(self, state: TaskState, work: Callable[[], Awaitable[Any]]) -> None:
        try:
            results = await work()
        except Exception as exc:  # noqa: BLE001 - 背景任務的例外沒有人接，一律記在狀態上
            state.error = f"{type(exc).__name__}: {exc}"
            logger.exception("階段 %s 執行失敗", state.stage)
        else:
            state.results = list(results) if isinstance(results, list) else [results]
        finally:
            state.finished_at = self._clock()
            self._history[state.stage] = state
            self._current = None
            self._task = None

    async def wait(self) -> None:
        """等目前的任務結束。測試用；正式流程一律用進度查詢。"""
        task = self._task
        if task is not None:
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
