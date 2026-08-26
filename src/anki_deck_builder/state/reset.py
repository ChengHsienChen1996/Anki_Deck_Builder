"""工作檔的重置與清空（狀態層）。

三個強度，由呼叫端選：

| 動作 | 做什麼 | 破壞性 |
|------|--------|--------|
| `reset_stages()` | 指定階段的狀態設回 `pending`、清空該階段的 `*_error` | 否——內容欄位一字不動 |
| `clear_rows()` | 刪掉所有列，只留表頭 | 是 |
| `clear_rows(media_dirs=…)` | 同上，另刪指定目錄下的媒體檔 | 是 |

**任何動作前都先備份 `cards.csv`**（`cards.csv.bak-<時間戳>`）。這是這個模組唯一
主動做的「多餘」的事：重置的每一種用法都是「我確定不要現在的狀態了」，而人會弄錯，
一份備份的代價是幾百 KB。

要刪哪些媒體目錄由**呼叫端傳進來**，本模組不認得 `media/img` 這種路徑——
那是流程層的知識（`stages/pack.py` 的 `MEDIA_DIRS`），狀態層不該反過來依賴它
（約束 4：分層依賴單向）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..schemas import StageStatus
from .selector import stage_fields
from .store import CardStore

#: 備份檔名的時間戳格式。到秒——同一分鐘內重置兩次是常見的（改完設定再來一次）
BACKUP_STAMP = "%Y-%m-%d-%H%M%S"


@dataclass(frozen=True)
class ResetResult:
    """一次重置的結果統計。"""

    backup: Path | None
    stages: tuple[str, ...] = ()
    #: 狀態真的被改動的列數（本來就是 pending 的不算）
    affected_rows: int = 0
    cleared_rows: int = 0
    removed_media: int = 0
    #: 刪不掉的媒體檔（權限、檔案被佔用），呼叫端負責顯示
    failures: tuple[str, ...] = field(default_factory=tuple)


async def backup_work(store: CardStore) -> Path | None:
    """把目前的中間 CSV 複製一份，回傳備份路徑。工作檔不存在時回 `None`。"""
    if not store.exists():
        return None

    source = Path(store.path)
    target = source.with_name(f"{source.name}.bak-{datetime.now().strftime(BACKUP_STAMP)}")

    def copy() -> None:
        target.write_bytes(source.read_bytes())

    await asyncio.to_thread(copy)
    return target


async def reset_stages(
    store: CardStore, stages: Sequence[str], backup: bool = True
) -> ResetResult:
    """把指定階段的狀態設回 `pending`。

    **內容欄位一字不動**——包括 `image_front` 這類媒體路徑。重跑該階段時會覆寫，
    在那之前舊檔案仍在、仍看得到，這是刻意的：重置狀態不等於丟掉成果。

    Args:
        stages: 階段名稱，須為 `STAGE_FIELDS` 的鍵。
        backup: 是否先備份工作檔。

    Raises:
        ValueError: 階段名稱不合法，訊息附上全部合法值。
        WorkFileError: 中間 CSV 讀寫失敗。
    """
    fields = [_fields_for(stage) for stage in stages]
    if not fields:
        raise ValueError("沒有指定要重置的階段")

    saved = await backup_work(store) if backup else None
    rows = await store.read() if store.exists() else []

    affected = 0
    for row in rows:
        changed = False
        for stage_field in fields:
            if getattr(row, stage_field.status) is not StageStatus.PENDING:
                setattr(row, stage_field.status, StageStatus.PENDING)
                changed = True
            if getattr(row, stage_field.error):
                setattr(row, stage_field.error, "")
                changed = True
        affected += changed

    if affected:
        await store.write(rows)

    return ResetResult(backup=saved, stages=tuple(stages), affected_rows=affected)


async def clear_rows(
    store: CardStore, media_dirs: Sequence[Path] = (), backup: bool = True
) -> ResetResult:
    """刪掉工作檔內所有列（保留檔案與表頭），可選擇一併刪除媒體檔。

    只刪 `media_dirs` **第一層的檔案**，不遞迴、不刪目錄本身——目錄留著，
    下一批教材照樣寫得進去；不遞迴則避免使用者把別的東西放進 `work/` 而被掃掉。

    Raises:
        WorkFileError: 中間 CSV 讀寫失敗。
    """
    saved = await backup_work(store) if backup else None
    rows = await store.read() if store.exists() else []

    await store.write([])
    removed, failures = await asyncio.to_thread(_remove_files, media_dirs)

    return ResetResult(
        backup=saved,
        cleared_rows=len(rows),
        removed_media=removed,
        failures=failures,
    )


async def count_media(media_dirs: Sequence[Path]) -> int:
    """媒體目錄第一層有幾個檔案。供介面層在確認提示中說出「會刪掉幾個」。"""
    return await asyncio.to_thread(
        lambda: sum(1 for path in _iter_files(media_dirs))
    )


def _fields_for(stage: str):  # noqa: ANN202 - StageFields，避免為型別多一個匯入
    try:
        return stage_fields(stage)
    except KeyError as error:
        # stage_fields 的訊息已含全部合法值，原樣轉成 ValueError：
        # KeyError 的 str() 會多一層引號，直接顯示給使用者很難看
        raise ValueError(error.args[0]) from None


def _iter_files(media_dirs: Sequence[Path]):  # noqa: ANN202 - Iterator[Path]
    for directory in media_dirs:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file():
                yield path


def _remove_files(media_dirs: Sequence[Path]) -> tuple[int, tuple[str, ...]]:
    removed = 0
    failures: list[str] = []
    for path in _iter_files(media_dirs):
        try:
            path.unlink()
        except OSError as error:
            failures.append(f"{path.name}：{error}")
        else:
            removed += 1
    return removed, tuple(failures)
