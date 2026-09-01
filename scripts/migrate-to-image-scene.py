#!/usr/bin/env python
"""把 Phase 8 之前的中間 CSV 遷移到含「語義層」的新格式。

做兩件事：

1. **補上 Phase 8 新增的五個欄位**：`image_scene`、`scene_status`／`scene_error`、
   `prompt_status`／`prompt_error`。這一步是**必要的**，不是選配——
   `CardStore.read()` 會逐欄比對表頭與 `CardRow.field_order()`，不一致就拋
   `WorkFileError`，所以舊檔在補欄之前**完全讀不進來**。
2. **從既有的 `image_prompt` 切出 `image_scene`**：舊資料把「場景語義」與
   「模型專屬的統一風格後綴」黏成同一個字串，切掉後綴剩下的就是語義層。
   後綴是逐字固定的（2026-09-01 實測 308/308 與 13/13 皆以該串結尾），
   因此**這是確定性字串處理，不呼叫任何模型**。

切得成功的列，`scene_status` 與 `prompt_status` 一併設為 `done`——那兩層的內容
都已經有了。切不成功的（`image_prompt` 為空，或結尾不是已知後綴）**一律留 pending
並列出來**，交給 `scene`／`prompt` 階段重生，不臆測、不硬切。

用法：

    uv run python scripts/migrate-to-image-scene.py work/cards.csv --dry-run
    uv run python scripts/migrate-to-image-scene.py work/cards.csv

重複執行是安全的：已經是新格式的檔案只會補做第 2 步，已有 `image_scene` 的列不動。

> **為什麼不用 `CardStore`**：它的 `read()` 就是那道會擋下舊檔的表頭檢查，
> 而本腳本的工作正是把檔案變成它讀得進去的樣子。所以這裡直接用 `csv` 模組讀，
> 但寫回時比照 `state/store.py`：同樣的編碼、同樣的原子寫入（約束 2）。
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime
from pathlib import Path

from anki_deck_builder.schemas import CardRow, StageStatus

#: 與 `state/store.py` 一致——工作檔由它產出，遷移後的位元格式必須對得上
ENCODING = "utf-8-sig"

#: Phase 8 新增的五個欄位
NEW_FIELDS: tuple[str, ...] = (
    "image_scene",
    "scene_status",
    "scene_error",
    "prompt_status",
    "prompt_error",
)

#: 統一風格後綴。Phase 8 之前由 `prompts/extract_cards.md` 要求模型逐字接在
#: 每個 `image_prompt` 結尾，權威定義在 `prompts/image_prompt_template.md`。
#: **這裡是歷史值的副本**：它描述的是「舊資料長什麼樣」，不是現在的設定，
#: 所以即使日後 profile 換了後綴，本腳本這個常數也不該跟著改
LEGACY_STYLE_SUFFIX = (
    ", cinematic lighting, muted color palette, soft shadows, atmospheric, "
    "no text, no letters, no watermark"
)


def split_legacy_prompt(prompt: str) -> str | None:
    """切掉統一風格後綴，回傳語義層；切不出來時回 `None`。

    回 `None` 的兩種情形——空字串、結尾不是已知後綴——呼叫端一律留 pending
    交給 `scene` 階段重生。**寧可重生也不要硬切**：切錯會把風格詞留在語義層，
    換模型時那些詞會跟著被帶進新 profile，正是 Phase 8 要消除的問題。
    """
    text = prompt.strip()
    if not text or not text.endswith(LEGACY_STYLE_SUFFIX):
        return None
    scene = text[: -len(LEGACY_STYLE_SUFFIX)].strip()
    # 後綴本身以逗號開頭，切掉後場景結尾不該再留一個孤逗號
    return scene.rstrip(",").strip() or None


def migrate_rows(
    header: list[str], body: list[list[str]]
) -> tuple[list[dict[str, str]], list[str]]:
    """把舊表頭與資料列遷移成新格式。

    Returns:
        (新格式的列, 切不出語義層的 card_id 清單)

    Raises:
        ValueError: 表頭既不是遷移前的舊格式，也不是遷移後的新格式。
    """
    expected = list(CardRow.field_order())
    missing = [name for name in expected if name not in header]
    extra = [name for name in header if name not in expected]
    if extra or (missing and missing != list(NEW_FIELDS)):
        raise ValueError(
            "表頭無法辨識，既不是 Phase 8 之前的格式也不是之後的格式：\n"
            f"  缺少 {missing}\n  多出 {extra}"
        )

    index = {name: position for position, name in enumerate(header)}
    # 舊檔缺的欄位一律回退 `CardRow` 的預設值（狀態欄因此是 pending 而非空字串），
    # 與 `from_csv_row()` 對缺值的處理一致，寫出的檔案才與 `CardStore.write()` 同形
    defaults = CardRow().to_csv_row()
    rows: list[dict[str, str]] = []
    unsplit: list[str] = []

    for values in body:
        row = {
            name: (values[index[name]] if name in index else defaults[name])
            for name in expected
        }

        # 已經有語義層的列不動——重複執行本腳本不該覆蓋任何既有內容
        if row["image_scene"].strip():
            rows.append(row)
            continue

        scene = split_legacy_prompt(row["image_prompt"])
        if scene is None:
            # image_prompt 為空的列多半是 raw_text 來源列，本來就不該有圖，
            # 不列進待處理清單；有 prompt 卻切不開的才需要人看
            if row["image_prompt"].strip():
                unsplit.append(row["card_id"] or "(無 card_id)")
        else:
            row["image_scene"] = scene
            row["scene_status"] = StageStatus.DONE.value
            row["prompt_status"] = StageStatus.DONE.value
        rows.append(row)

    return rows, unsplit


def write_atomic(path: Path, rows: list[dict[str, str]]) -> None:
    """比照 `state/store.py`：寫暫存檔 → fsync → `os.replace`（約束 2）。"""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(CardRow.field_order())
    for row in rows:
        writer.writerow([row[name] for name in CardRow.field_order()])

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding=ENCODING, newline="") as fh:
        fh.write(buffer.getvalue())
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="中間 CSV 路徑，例如 work/cards.csv")
    parser.add_argument("--dry-run", action="store_true", help="只報告，不寫檔")
    args = parser.parse_args()

    path: Path = args.csv_path
    if not path.exists():
        print(f"檔案不存在：{path}", file=sys.stderr)
        return 1

    with path.open(encoding=ENCODING, newline="") as fh:
        table = list(csv.reader(fh))
    if not table:
        print(f"檔案沒有表頭：{path}", file=sys.stderr)
        return 1

    header, body = table[0], table[1:]
    already_migrated = header == list(CardRow.field_order())

    try:
        rows, unsplit = migrate_rows(header, body)
    except ValueError as exc:
        print(f"{path}：{exc}", file=sys.stderr)
        return 1

    seeded = sum(1 for row in rows if row["image_scene"].strip())
    print(f"{path}：{len(rows)} 列")
    print(f"  表頭        {'已是新格式' if already_migrated else f'{len(header)} 欄 → 44 欄'}")
    print(f"  image_scene {seeded} 列有值")
    if unsplit:
        print(f"  切不出語義層 {len(unsplit)} 列（留 pending，交給 scene 階段重生）：")
        for card_id in unsplit[:10]:
            print(f"    {card_id}")
        if len(unsplit) > 10:
            print(f"    …另外 {len(unsplit) - 10} 列")

    if args.dry_run:
        print("  --dry-run：未寫檔")
        return 0

    backup = path.with_name(f"{path.name}.bak-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}")
    backup.write_bytes(path.read_bytes())
    write_atomic(path, rows)
    print(f"  已寫回，備份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
