"""CardRow 單元測試。

重點在**欄位順序的穩定性**：順序一旦錯位，後續所有階段都會錯。
"""

import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from anki_deck_builder.schemas import (
    ENGINE_FIELDS,
    ENGINE_MANAGED_FIELDS,
    INTERMEDIATE_FIELDS,
    STATUS_FIELDS,
    CardRow,
    StageStatus,
)

# ── 欄位順序 ─────────────────────────────────────────────────────


def test_group_sizes_match_architecture() -> None:
    assert len(ENGINE_FIELDS) == 23
    assert len(INTERMEDIATE_FIELDS) == 7
    assert len(STATUS_FIELDS) == 14
    assert len(CardRow.field_order()) == 44


def test_groups_are_disjoint() -> None:
    assert not set(ENGINE_FIELDS) & set(INTERMEDIATE_FIELDS)
    assert not set(ENGINE_FIELDS) & set(STATUS_FIELDS)
    assert not set(INTERMEDIATE_FIELDS) & set(STATUS_FIELDS)


def test_field_order_is_a_then_b_then_c() -> None:
    assert CardRow.field_order() == ENGINE_FIELDS + INTERMEDIATE_FIELDS + STATUS_FIELDS


def test_declaration_order_matches_field_order() -> None:
    """model 的宣告順序若與 field_order() 脫節，序列化就會錯位。"""
    assert tuple(CardRow.model_fields) == CardRow.field_order()


def test_field_order_matches_golden_csv_header(expected_cards_csv: Path) -> None:
    """以對齊樣板的表頭作為欄位順序的外部基準。"""
    with expected_cards_csv.open(encoding="utf-8-sig", newline="") as fh:
        header = tuple(next(csv.reader(fh)))
    assert header == CardRow.field_order()


def test_engine_field_order_excludes_intermediate_and_status() -> None:
    engine = CardRow.engine_field_order()
    assert engine == ENGINE_FIELDS
    assert not set(engine) & set(INTERMEDIATE_FIELDS + STATUS_FIELDS)


def test_engine_managed_fields_are_engine_fields() -> None:
    assert set(ENGINE_MANAGED_FIELDS) <= set(ENGINE_FIELDS)


# ── 預設值 ───────────────────────────────────────────────────────


def test_defaults() -> None:
    row = CardRow()

    assert row.card_id == ""
    assert row.image_front == ""
    assert row.tts_back_text == ""
    assert row.ocr_source_page is None
    for name in STATUS_FIELDS:
        expected = StageStatus.PENDING if name.endswith("_status") else ""
        assert getattr(row, name) == expected


def test_phase_3_and_4_fields_exist_now() -> None:
    """Phase 1 不填，但欄位現在就要在。"""
    row = CardRow()
    for name in (
        "image_prompt",
        "tts_front_text",
        "tts_back_text",
        "image_status",
        "audio_front_status",
        "audio_back_status",
    ):
        assert name in CardRow.model_fields
        assert getattr(row, name) is not None


# ── 序列化 ───────────────────────────────────────────────────────


def test_to_csv_row_key_order_matches_field_order() -> None:
    assert tuple(CardRow().to_csv_row()) == CardRow.field_order()


def test_to_csv_row_values_are_all_strings() -> None:
    row = CardRow(card_id="a1", ocr_source_page=3, ocr_status=StageStatus.DONE)

    values = row.to_csv_row()

    assert all(isinstance(v, str) for v in values.values())
    assert values["ocr_status"] == "done"
    assert values["ocr_source_page"] == "3"


def test_to_csv_row_writes_empty_string_for_none_page() -> None:
    assert CardRow().to_csv_row()["ocr_source_page"] == ""


def test_round_trip_preserves_all_fields() -> None:
    row = CardRow(
        card_id="ja_n2_001",
        deck="日語::N2::動詞",
        front="属する",
        back="屬於，歸於；隸屬，附屬",
        example="虎はネコ科に属する。\\n老虎屬於貓科。",
        note='含逗號, 引號" 與換行\\n的內容',
        ocr_source_page=1,
        reading="ぞくする",
        ocr_status=StageStatus.DONE,
        extract_status=StageStatus.FAILED,
        extract_error="card_id duplicated",
    )

    restored = CardRow.from_csv_row(row.to_csv_row())

    assert restored == row


def test_from_csv_row_parses_golden_fixture(expected_cards_csv: Path) -> None:
    with expected_cards_csv.open(encoding="utf-8-sig", newline="") as fh:
        raw_rows = list(csv.DictReader(fh))

    rows = [CardRow.from_csv_row(raw) for raw in raw_rows]

    assert len(rows) == 16
    assert rows[0].card_id == "fx_n2_001"
    assert rows[0].reading == "ぞくする"
    assert rows[0].ocr_source_page == 1
    assert rows[0].ocr_status is StageStatus.DONE
    assert rows[0].extract_status is StageStatus.DONE
    assert rows[0].image_status is StageStatus.PENDING
    # 系統欄位一律留空，由記憶引擎管理
    for row in rows:
        for name in ENGINE_MANAGED_FIELDS:
            assert getattr(row, name) == ""


def test_from_csv_row_ignores_unknown_columns() -> None:
    row = CardRow.from_csv_row({"card_id": "a1", "legacy_column": "x"})

    assert row.card_id == "a1"


def test_from_csv_row_treats_empty_string_as_default() -> None:
    row = CardRow.from_csv_row({"ocr_source_page": "", "ocr_status": ""})

    assert row.ocr_source_page is None
    assert row.ocr_status is StageStatus.PENDING


def test_from_csv_row_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        CardRow.from_csv_row({"extract_status": "skipped"})


def test_from_csv_row_rejects_non_numeric_page() -> None:
    with pytest.raises(ValidationError):
        CardRow.from_csv_row({"ocr_source_page": "第一頁"})


# ── 嚴格性 ───────────────────────────────────────────────────────


def test_unknown_field_is_rejected() -> None:
    """欄位打錯時要當場報錯，不要靜默吞掉。"""
    with pytest.raises(ValidationError):
        CardRow(cardid="a1")


def test_assignment_is_validated() -> None:
    row = CardRow()
    with pytest.raises(ValidationError):
        row.extract_status = "skipped"

    row.extract_status = StageStatus.DONE
    assert row.extract_status is StageStatus.DONE
