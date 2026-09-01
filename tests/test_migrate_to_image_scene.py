"""`scripts/migrate-to-image-scene.py` 的測試。

腳本會改寫使用者的工作檔，切錯就是把風格詞留在語義層、換模型時被帶進新 profile
（正是 Phase 8 要消除的問題），因此雖然它是一次性腳本仍然測。

`scripts/` 不是套件，以 importlib 由路徑載入。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from anki_deck_builder.schemas import CardRow, StageStatus

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate-to-image-scene.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migrate_to_image_scene", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


migrate = _load()

#: 遷移前的表頭：新格式扣掉 Phase 8 的五個欄位
OLD_HEADER = [name for name in CardRow.field_order() if name not in migrate.NEW_FIELDS]

SUFFIX = migrate.LEGACY_STYLE_SUFFIX


def _old_row(**values: str) -> list[str]:
    row = dict.fromkeys(OLD_HEADER, "")
    row.update(values)
    return [row[name] for name in OLD_HEADER]


# ── 後綴切分 ─────────────────────────────────────────────────────


def test_splits_off_the_style_suffix() -> None:
    scene = migrate.split_legacy_prompt(f"a tiger among cats, taxonomy mood{SUFFIX}")
    assert scene == "a tiger among cats, taxonomy mood"


def test_split_leaves_no_trailing_comma() -> None:
    """後綴本身以逗號開頭，切掉後不該留下孤逗號。"""
    assert not migrate.split_legacy_prompt(f"a lone scene{SUFFIX}").endswith(",")


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "   ",
        "a scene with no suffix at all",
        "a scene, cinematic lighting, muted color palette",  # 只有半截後綴
        SUFFIX.lstrip(", "),  # 只有後綴、沒有場景
    ],
)
def test_returns_none_when_suffix_is_absent(prompt: str) -> None:
    """切不出來一律回 None——寧可讓 scene 階段重生，也不要硬切。"""
    assert migrate.split_legacy_prompt(prompt) is None


# ── 整份遷移 ─────────────────────────────────────────────────────


def test_adds_the_five_new_columns() -> None:
    rows, _ = migrate.migrate_rows(OLD_HEADER, [_old_row(card_id="a")])

    assert set(rows[0]) == set(CardRow.field_order())


def test_seeds_scene_and_marks_both_layers_done() -> None:
    body = [_old_row(card_id="a", image_prompt=f"a quiet library{SUFFIX}")]

    rows, unsplit = migrate.migrate_rows(OLD_HEADER, body)

    assert rows[0]["image_scene"] == "a quiet library"
    assert rows[0]["scene_status"] == StageStatus.DONE.value
    assert rows[0]["prompt_status"] == StageStatus.DONE.value
    assert rows[0]["image_prompt"] == f"a quiet library{SUFFIX}"  # 語法層原樣保留
    assert unsplit == []


def test_unsplittable_row_stays_pending_and_is_reported() -> None:
    body = [_old_row(card_id="a", image_prompt="a scene without the suffix")]

    rows, unsplit = migrate.migrate_rows(OLD_HEADER, body)

    assert rows[0]["image_scene"] == ""
    assert rows[0]["scene_status"] == StageStatus.PENDING.value
    assert rows[0]["prompt_status"] == StageStatus.PENDING.value
    assert unsplit == ["a"]


def test_row_without_prompt_is_not_reported() -> None:
    """raw_text 來源列本來就沒有 image_prompt，不該被列成待處理。"""
    body = [_old_row(card_id="", raw_text="page text")]

    rows, unsplit = migrate.migrate_rows(OLD_HEADER, body)

    assert rows[0]["image_scene"] == ""
    assert unsplit == []


def test_existing_scene_is_never_overwritten() -> None:
    """重複執行要安全：已有語義層的列不動（可能是人工編修過的）。"""
    header = list(CardRow.field_order())
    row = dict.fromkeys(header, "")
    row.update(card_id="a", image_scene="hand edited", image_prompt=f"other{SUFFIX}")

    rows, _ = migrate.migrate_rows(header, [[row[name] for name in header]])

    assert rows[0]["image_scene"] == "hand edited"


def test_migrating_twice_is_idempotent() -> None:
    body = [_old_row(card_id="a", image_prompt=f"a quiet library{SUFFIX}")]

    once, _ = migrate.migrate_rows(OLD_HEADER, body)
    header = list(CardRow.field_order())
    twice, _ = migrate.migrate_rows(header, [[row[name] for name in header] for row in once])

    assert twice == once


def test_unrecognised_header_raises() -> None:
    with pytest.raises(ValueError, match="表頭無法辨識"):
        migrate.migrate_rows(["card_id", "who_is_this"], [])


# ── 寫回 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_card_store_reads_migrated_file(tmp_path: Path) -> None:
    from anki_deck_builder.state import CardStore

    rows, _ = migrate.migrate_rows(
        OLD_HEADER, [_old_row(card_id="a", image_prompt=f"a quiet library{SUFFIX}")]
    )
    path = tmp_path / "cards.csv"
    migrate.write_atomic(path, rows)

    loaded = await CardStore(path).read()

    assert len(loaded) == 1
    assert loaded[0].image_scene == "a quiet library"
    assert loaded[0].scene_status is StageStatus.DONE
    assert loaded[0].prompt_status is StageStatus.DONE
