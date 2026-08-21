"""待處理列篩選與狀態更新（狀態層）。

**階段名稱與狀態欄位的對應表集中於此**（`STAGE_FIELDS`），其他模組一律呼叫本模組取用，
不得自行以字串組出 `f"{stage}_status"`——`audio` 拆成 `audio_front` 與 `audio_back`
兩個獨立階段，字串組合會漏掉這個特例。

本層不知道任何階段的業務語義（約束 4），只認得「哪一欄是哪一階段的狀態」。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..schemas import CardRow, StageStatus


@dataclass(frozen=True)
class StageFields:
    """單一階段在中間 CSV 上對應的兩個欄位。"""

    status: str
    error: str


#: 階段名稱 → 狀態／錯誤欄位。`audio_front` 與 `audio_back` 狀態獨立，任一失敗不影響另一個。
STAGE_FIELDS: dict[str, StageFields] = {
    "ocr": StageFields("ocr_status", "ocr_error"),
    "extract": StageFields("extract_status", "extract_error"),
    "image": StageFields("image_status", "image_error"),
    "audio_front": StageFields("audio_front_status", "audio_front_error"),
    "audio_back": StageFields("audio_back_status", "audio_back_error"),
}

#: 供 `status` 子命令與測試走訪，順序即五階段的執行順序
STAGE_NAMES: tuple[str, ...] = tuple(STAGE_FIELDS)


def stage_fields(stage: str) -> StageFields:
    """取得階段對應的欄位名。

    Raises:
        KeyError: 階段名稱不存在，訊息附上全部合法名稱。
    """
    try:
        return STAGE_FIELDS[stage]
    except KeyError:
        raise KeyError(
            f"未知的階段名稱 {stage!r}，合法值：{', '.join(STAGE_NAMES)}"
        ) from None


def get_status(row: CardRow, stage: str) -> StageStatus:
    return getattr(row, stage_fields(stage).status)


def select_pending(
    rows: Sequence[CardRow],
    stage: str,
    force: bool = False,
    only_failed: bool = False,
) -> list[CardRow]:
    """篩選待處理列（依 architecture.md〈篩選規則〉）。

    | 條件 | 納入的狀態 |
    |------|-----------|
    | 預設 | `pending` + `failed` |
    | `only_failed` | 僅 `failed` |
    | `force` | 全部（含 `done`） |

    Raises:
        ValueError: `force` 與 `only_failed` 同時為真（兩者互斥）。
    """
    if force and only_failed:
        raise ValueError("--force 與 --only-failed 互斥，不可同時指定")

    fields = stage_fields(stage)
    if force:
        return list(rows)

    wanted = (
        {StageStatus.FAILED}
        if only_failed
        else {StageStatus.PENDING, StageStatus.FAILED}
    )
    return [row for row in rows if getattr(row, fields.status) in wanted]


def mark_done(row: CardRow, stage: str) -> None:
    """標記成功。同時清空錯誤欄位——重跑成功後舊錯誤訊息不該留著誤導。"""
    fields = stage_fields(stage)
    setattr(row, fields.status, StageStatus.DONE)
    setattr(row, fields.error, "")


def mark_failed(row: CardRow, stage: str, error: str) -> None:
    """標記失敗並記錄原因（約束 3：記錄後跳過，不中斷整批）。"""
    fields = stage_fields(stage)
    setattr(row, fields.status, StageStatus.FAILED)
    setattr(row, fields.error, error)


def summarize(rows: Iterable[CardRow]) -> dict[str, dict[StageStatus, int]]:
    """各階段的狀態統計，供 `status` 子命令輸出。"""
    summary = {
        stage: dict.fromkeys(StageStatus, 0) for stage in STAGE_NAMES
    }
    for row in rows:
        for stage, fields in STAGE_FIELDS.items():
            summary[stage][getattr(row, fields.status)] += 1
    return summary


def failed_rows(rows: Iterable[CardRow], stage: str) -> list[CardRow]:
    """取出該階段失敗的列，供 `status` 輸出失敗明細與 `pack` 的中止判斷。"""
    fields = stage_fields(stage)
    return [row for row in rows if getattr(row, fields.status) is StageStatus.FAILED]
