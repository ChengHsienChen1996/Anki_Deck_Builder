"""長時間階段的進度顯示（流程層）。

    生成聯想圖  [████████░░░░░░░░]  42/100  已耗時 08:15  預估剩餘 11:22

以標準庫實作，不引入 tqdm——需要的只是「一行字加 `\\r`」，為此多一個相依不划算。

## 本階段實作，Phase 4 沿用

`label` 是唯一與階段有關的東西，其餘全是計數與時間換算。Phase 4 的 audio
直接 `ProgressReporter("生成語音", total)`，不需要另寫。

## 非 TTY 降級

`\\r` 在 log 檔裡是控制字元，一百張圖會留下一百段疊在一起的亂碼。因此
不是終端機時改成每完成 `plain_every` 項印一行完整的（含換行），並省去進度條——
log 要的是「跑到哪了」，不是 16 個方塊字元。

## 預估剩餘只看平均速度

不做加權或滑動平均：本專案每項的耗時相當一致（同一個模型、同樣的尺寸），
平均值就夠準，而複雜的估計法在前幾項時反而更不穩。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import TextIO

#: 進度條寬度（字元）
BAR_WIDTH = 16
#: 非 TTY 時每完成幾項印一行
PLAIN_EVERY = 10

_FILLED = "█"
_EMPTY = "░"
#: 還沒有樣本可推估時的佔位
_UNKNOWN = "--:--"


class ProgressReporter:
    """把「完成幾項」換算成一行進度。

    不知道任何階段的業務語義，只認得計數與時間。
    """

    def __init__(
        self,
        label: str,
        total: int,
        stream: TextIO | None = None,
        plain_every: int = PLAIN_EVERY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            label: 顯示在最前面的階段名稱，例如「生成聯想圖」。
            total: 總項數。為 0 時本物件完全不輸出。
            stream: 輸出目的地，預設 `sys.stderr`——進度是給人看的，
                不該混進被導向檔案的正式輸出。
            plain_every: 非 TTY 時每完成幾項印一行。
            clock: 計時來源，測試以假時鐘替換。
        """
        self.label = label
        self.total = total
        self.plain_every = max(1, plain_every)
        self._stream = stream if stream is not None else sys.stderr
        self._clock = clock
        self._done = 0
        self._started = clock()
        self._live = _is_tty(self._stream)

    @property
    def done(self) -> int:
        return self._done

    def start(self) -> None:
        """在第一項完成前先顯示 0/total，讓使用者知道總量與程式沒卡住。"""
        if self.total and self._live:
            self._write_live()

    def advance(self, count: int = 1) -> None:
        """記錄完成了幾項並刷新顯示。"""
        if not self.total:
            return
        self._done = min(self.total, self._done + count)
        if self._live:
            self._write_live()
        elif self._done % self.plain_every == 0 or self._done == self.total:
            self._write_plain()

    def finish(self) -> None:
        """收尾：終端機模式補一個換行，把游標讓給後續輸出。"""
        if self.total and self._live:
            self._write_live()
            self._stream.write("\n")
            self._flush()

    # ── 輸出 ─────────────────────────────────────────────────────

    def _write_live(self) -> None:
        self._stream.write("\r" + self.render())
        self._flush()

    def _write_plain(self) -> None:
        self._stream.write(self.render(bar=False) + "\n")
        self._flush()

    def render(self, bar: bool = True) -> str:
        """組出一行進度文字。`bar=False` 省去進度條，供 log 使用。"""
        elapsed = self._clock() - self._started
        parts = [self.label]
        if bar:
            parts.append(f"[{self._bar()}]")
        parts.append(f"{self._done}/{self.total}")
        parts.append(f"已耗時 {format_duration(elapsed)}")
        parts.append(f"預估剩餘 {self._remaining(elapsed)}")
        return "  ".join(parts)

    def _bar(self) -> str:
        filled = BAR_WIDTH * self._done // self.total if self.total else 0
        return _FILLED * filled + _EMPTY * (BAR_WIDTH - filled)

    def _remaining(self, elapsed: float) -> str:
        """依平均速度推估。還沒有任何一項完成時無從估計。"""
        if self._done >= self.total:
            return format_duration(0)
        if self._done <= 0 or elapsed <= 0:
            return _UNKNOWN
        per_item = elapsed / self._done
        return format_duration(per_item * (self.total - self._done))

    def _flush(self) -> None:
        flush = getattr(self._stream, "flush", None)
        if callable(flush):
            flush()


def format_duration(seconds: float) -> str:
    """秒數轉 `MM:SS`；超過一小時才加上小時欄位。

    >>> format_duration(495)
    '08:15'
    >>> format_duration(3661)
    '1:01:01'
    """
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _is_tty(stream: TextIO) -> bool:
    """串流是不是終端機。取不到 `isatty` 的一律當作非終端機。"""
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except ValueError:  # pragma: no cover - 串流已關閉
        return False
