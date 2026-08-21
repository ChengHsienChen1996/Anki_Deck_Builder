"""中間 CSV 的異步讀寫（狀態層）。

依 docs/architecture.md〈CSV 格式規格〉：

| 項目 | 規格 |
|------|------|
| 編碼 | UTF-8 with BOM |
| 分隔符 | 半形逗號 |
| 引號 | 半形雙引號，含逗號／換行／引號時包覆 |
| 欄位內換行 | 使用 `\\n` 字面值 |
| 欄位順序 | `CardRow.field_order()`，須穩定不變 |

**原子寫入（約束 2）**：一律寫暫存檔再 `os.replace` rename。圖生成一批可能跑 30 分鐘，
中途被 Ctrl-C／OOM／斷電打斷時，直接覆寫會讓整份工作檔連同前面所有階段的成果一起損毀。

本層不知道任何階段的業務語義（約束 4），只負責「一列一列地存取」。
"""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Sequence
from pathlib import Path

import aiofiles

from ..exceptions import WorkFileError
from ..schemas import CardRow

ENCODING = "utf-8-sig"
TMP_SUFFIX = ".tmp"


class CardStore:
    """單一中間 CSV 的存取介面。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @property
    def tmp_path(self) -> Path:
        """原子寫入用的暫存檔路徑。"""
        return self.path.with_name(self.path.name + TMP_SUFFIX)

    def exists(self) -> bool:
        return self.path.exists()

    async def create_empty(self) -> None:
        """建立僅含表頭的檔案。已存在則不動它。"""
        if self.exists():
            return
        await self.write([])

    async def read(self) -> list[CardRow]:
        """讀入全部列。

        Raises:
            WorkFileError: 檔案不存在，或表頭與 `CardRow.field_order()` 不一致。
        """
        if not self.exists():
            raise WorkFileError(f"中間 CSV 不存在：{self.path}")

        async with aiofiles.open(self.path, encoding=ENCODING, newline="") as fh:
            content = await fh.read()

        reader = csv.reader(io.StringIO(content, newline=""))
        try:
            header = next(reader)
        except StopIteration:
            raise WorkFileError(f"中間 CSV 沒有表頭：{self.path}") from None

        expected = CardRow.field_order()
        if tuple(header) != expected:
            raise WorkFileError(
                f"中間 CSV 表頭與 CardRow.field_order() 不一致：{self.path}\n"
                f"  預期 {len(expected)} 欄，實際 {len(header)} 欄\n"
                f"  差異：缺少 {sorted(set(expected) - set(header))}、"
                f"多出 {sorted(set(header) - set(expected))}"
            )

        rows: list[CardRow] = []
        for line_no, values in enumerate(reader, start=2):
            if not values:  # 略過空行
                continue
            if len(values) != len(expected):
                raise WorkFileError(
                    f"中間 CSV 第 {line_no} 行有 {len(values)} 欄，應為 {len(expected)} 欄："
                    f"{self.path}"
                )
            rows.append(CardRow.from_csv_row(dict(zip(expected, values, strict=True))))
        return rows

    async def write(self, rows: Sequence[CardRow]) -> None:
        """原子地寫回全部列：先寫暫存檔、fsync，再 `os.replace` rename。

        rename 在同一個檔案系統上是原子操作，因此任何中斷點都只會留下
        「完全是舊內容」或「完全是新內容」，不會有寫到一半的檔案。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)

        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(CardRow.field_order())
        for row in rows:
            writer.writerow(row.to_csv_row().values())
        content = buffer.getvalue()

        tmp = self.tmp_path
        try:
            async with aiofiles.open(tmp, "w", encoding=ENCODING, newline="") as fh:
                await fh.write(content)
                await fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise WorkFileError(f"寫入中間 CSV 失敗：{self.path}（{exc}）") from exc
