"""階段 ⑤：路徑整合與打包（流程層）。

把中間 CSV 還原成引擎 schema 的 23 欄，連同媒體檔打包成記憶引擎可直接載入的 ZIP。

```
deck.zip
├── cards.csv          # 僅 (a) 引擎欄位，UTF-8 with BOM
└── media/
    ├── img/{card_id}.png
    └── audio/{card_id}_front.mp3
                {card_id}_back.mp3
```

**媒體欄位存的是 ZIP 內的相對路徑**（例 `media/img/ja_n2_001.png`），
磁碟上的實體檔案位於 `media_root / <該相對路徑>`。工作目錄與 ZIP 內同構，
打包只是照著搬——Phase 3、4 寫入這些欄位時請沿用此約定。

`pack` **不是 `BaseStage`**：它沒有逐列狀態欄位，是走完全部階段後的終端操作。

**來源列會被略過**：`extract` 保留了原始 `raw_text` 列供追溯，它們的 `card_id` 為空，
不是卡片，不進 ZIP。
"""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..exceptions import StageProcessingError
from ..schemas import ENGINE_MANAGED_FIELDS, CardRow, StageStatus
from ..state import CardStore
from ..state.selector import STAGE_FIELDS

#: 打包前必須有值的欄位
REQUIRED_FIELDS: tuple[str, ...] = ("card_id", "deck", "card_type", "front", "back")

#: 媒體欄位 → ZIP 內的目錄
MEDIA_FIELDS: tuple[str, ...] = ("image_front", "image_back", "audio_front", "audio_back")

#: 媒體欄位的中文類別，讓驗證訊息一眼看出缺的是圖還是音
MEDIA_KINDS: dict[str, str] = {
    "image_front": "聯想圖",
    "image_back": "聯想圖",
    "audio_front": "語音",
    "audio_back": "語音",
}

CSV_NAME = "cards.csv"
MEDIA_DIRS: tuple[str, ...] = ("media/", "media/img/", "media/audio/")


@dataclass(frozen=True)
class PackResult:
    """打包結果。"""

    output: Path
    card_count: int
    media_count: int
    skipped_source_rows: int


async def pack(
    store: CardStore,
    output_path: str | Path,
    media_root: str | Path | None = None,
    allow_failed: bool = False,
) -> PackResult:
    """打包成 ZIP。

    Args:
        store: 中間 CSV。
        output_path: 輸出的 ZIP 路徑。
        media_root: 媒體檔的根目錄，預設為中間 CSV 所在目錄。
        allow_failed: 允許在有 `failed` 列的情況下打包（略過那些列所屬的階段錯誤）。

    Raises:
        StageProcessingError: 存在 `failed` 列（且未指定 `allow_failed`），
            或完整性驗證未通過。訊息會列出**全部**問題，不是只報第一個。
        WorkFileError: 中間 CSV 讀取失敗。
    """
    output = Path(output_path)
    root = Path(media_root) if media_root is not None else store.path.parent

    rows = await store.read()
    if not allow_failed:
        _abort_on_failed(rows)

    cards, skipped = _split_rows(rows)
    _validate(cards, root)

    media = _collect_media(cards, root)
    await asyncio.to_thread(_write_zip, output, cards, media)

    return PackResult(
        output=output,
        card_count=len(cards),
        media_count=len(media),
        skipped_source_rows=skipped,
    )


# ── 檢查 ─────────────────────────────────────────────────────────


def _abort_on_failed(rows: list[CardRow]) -> None:
    """有失敗的列就中止，並列出清單。

    預設中止而非略過：失敗的卡片多半是使用者想修好的，靜靜少幾張卡很難察覺。
    """
    problems = [
        f"  {row.card_id or '(來源列)'}  {stage}: {getattr(row, fields.error) or '未記錄原因'}"
        for row in rows
        for stage, fields in STAGE_FIELDS.items()
        if getattr(row, fields.status) is StageStatus.FAILED
    ]
    if problems:
        raise StageProcessingError(
            f"有 {len(problems)} 項失敗，已中止打包（要略過請加 --allow-failed）：\n"
            + "\n".join(problems)
        )


def _split_rows(rows: list[CardRow]) -> tuple[list[CardRow], int]:
    """分出卡片列與 `extract` 留下的來源列。"""
    cards = [row for row in rows if row.card_id]
    skipped = sum(1 for row in rows if not row.card_id and row.raw_text)
    return cards, skipped


def _validate(cards: list[CardRow], media_root: Path) -> None:
    """完整性驗證。收齊所有問題後一次回報。"""
    problems: list[str] = []
    if not cards:
        problems.append("沒有任何卡片可打包")

    seen: dict[str, int] = {}
    for index, row in enumerate(cards, start=1):
        label = row.card_id or f"第 {index} 列"

        for field in REQUIRED_FIELDS:
            if not getattr(row, field).strip():
                problems.append(f"{label}：必填欄位 {field} 為空")

        if row.card_id:
            if row.card_id in seen:
                problems.append(
                    f"{row.card_id}：card_id 重複（第 {seen[row.card_id]} 與第 {index} 列）"
                )
            else:
                seen[row.card_id] = index

        for field in ENGINE_MANAGED_FIELDS:
            if getattr(row, field):
                problems.append(
                    f"{label}：系統欄位 {field} 應留空，由記憶引擎管理"
                    f"（收到 {getattr(row, field)!r}）"
                )

        for field in MEDIA_FIELDS:
            problem = _media_problem(row, field, media_root)
            if problem:
                problems.append(f"{label}：{problem}")

    if problems:
        listed = "\n".join(f"  {problem}" for problem in problems)
        raise StageProcessingError(
            f"完整性驗證未通過，共 {len(problems)} 項問題：\n{listed}"
        )


def _media_problem(row: CardRow, field: str, media_root: Path) -> str:
    """檢查單一媒體欄位，回傳問題描述；沒問題則回空字串。

    **零位元組也算失敗**：生成中途被中斷、或外部服務回傳空內容時會留下空檔案，
    `is_file()` 對它是 True，放行的話會打包出一張點了沒反應的卡。
    """
    relative = getattr(row, field)
    if not relative:
        return ""

    kind = MEDIA_KINDS.get(field, "媒體")
    path = media_root / relative
    if not path.is_file():
        return f"{kind} {field} 指向的檔案不存在（{path}）"
    if path.stat().st_size == 0:
        return f"{kind} {field} 指向的檔案是空的，0 位元組（{path}）"
    return ""


# ── 輸出 ─────────────────────────────────────────────────────────


def _collect_media(cards: list[CardRow], media_root: Path) -> dict[str, Path]:
    """ZIP 內相對路徑 → 磁碟實體路徑。同一個檔被多張卡引用時只收一份。"""
    media: dict[str, Path] = {}
    for row in cards:
        for field in MEDIA_FIELDS:
            relative = getattr(row, field)
            if relative:
                media[relative] = media_root / relative
    return media


def _render_csv(cards: list[CardRow]) -> bytes:
    """產生只含 23 欄引擎 schema 的 CSV，格式與中間 CSV 一致（BOM + CRLF）。"""
    engine_fields = CardRow.engine_field_order()
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(engine_fields)
    for row in cards:
        values = row.to_csv_row()
        writer.writerow([values[field] for field in engine_fields])
    return buffer.getvalue().encode("utf-8-sig")


def _write_zip(output: Path, cards: list[CardRow], media: dict[str, Path]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(CSV_NAME, _render_csv(cards))
        for directory in MEDIA_DIRS:
            # 明確寫入目錄項目，讓 Phase 1（無媒體）產出的 ZIP 也有完整結構
            archive.writestr(zipfile.ZipInfo(directory), b"")
        for relative, source in media.items():
            archive.write(source, relative)
