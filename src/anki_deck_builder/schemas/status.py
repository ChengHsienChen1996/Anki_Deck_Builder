"""階段狀態列舉。

狀態流轉（見 docs/architecture.md〈狀態流轉〉）：

    pending ──成功──→ done
       │               ▲
     失敗              │ 重跑成功
       ▼               │
    failed ────────────┘
"""

from enum import StrEnum


class StageStatus(StrEnum):
    """單一階段對單一列的處理狀態。

    `StrEnum`（Python 3.11+）等價於 architecture.md 寫的 `(str, Enum)`，
    但 `str(status)` 直接得到 `"pending"` 而非 `"StageStatus.PENDING"`，寫 CSV 較不易出錯。
    """

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
