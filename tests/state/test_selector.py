"""selector 單元測試（非付費模組，AI 執行至通過）。"""

import pytest

from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.state import (
    STAGE_FIELDS,
    STAGE_NAMES,
    failed_rows,
    get_status,
    mark_done,
    mark_failed,
    select_pending,
    stage_fields,
    summarize,
)


def _row(stage: str, status: StageStatus, card_id: str = "x") -> CardRow:
    return CardRow(card_id=card_id, **{stage_fields(stage).status: status})


# ── 對應表 ───────────────────────────────────────────────────────


def test_stage_names_are_in_execution_order() -> None:
    assert STAGE_NAMES == (
        "ocr",
        "extract",
        "scene",
        "prompt",
        "image",
        "audio_front",
        "audio_back",
    )


def test_scene_and_prompt_stages_are_separate() -> None:
    """語義層與語法層狀態獨立——換文生圖模型時只重生語法層，不洗掉語義層。"""
    assert stage_fields("scene").status != stage_fields("prompt").status


def test_every_mapped_field_exists_on_card_row() -> None:
    for fields in STAGE_FIELDS.values():
        assert fields.status in CardRow.model_fields
        assert fields.error in CardRow.model_fields


def test_audio_stages_are_separate() -> None:
    """audio_front 與 audio_back 狀態獨立，任一失敗不影響另一個。"""
    assert stage_fields("audio_front").status != stage_fields("audio_back").status


def test_unknown_stage_lists_valid_names() -> None:
    with pytest.raises(KeyError) as excinfo:
        stage_fields("audio")

    assert "audio_front" in str(excinfo.value)


def test_get_status_reads_the_right_column() -> None:
    row = CardRow(image_status=StageStatus.FAILED)

    assert get_status(row, "image") is StageStatus.FAILED
    assert get_status(row, "extract") is StageStatus.PENDING


# ── 篩選規則 ─────────────────────────────────────────────────────


def test_select_on_empty_input() -> None:
    assert select_pending([], "extract") == []
    assert select_pending([], "extract", force=True) == []
    assert select_pending([], "extract", only_failed=True) == []


@pytest.mark.parametrize(
    ("status", "in_default", "in_only_failed"),
    [
        (StageStatus.PENDING, True, False),
        (StageStatus.FAILED, True, True),
        (StageStatus.DONE, False, False),
    ],
)
def test_single_row_rules(
    status: StageStatus, in_default: bool, in_only_failed: bool
) -> None:
    rows = [_row("extract", status)]

    assert bool(select_pending(rows, "extract")) is in_default
    assert bool(select_pending(rows, "extract", only_failed=True)) is in_only_failed
    assert select_pending(rows, "extract", force=True) == rows


def test_all_failed() -> None:
    rows = [_row("extract", StageStatus.FAILED, f"c{i}") for i in range(3)]

    assert select_pending(rows, "extract") == rows
    assert select_pending(rows, "extract", only_failed=True) == rows
    assert select_pending(rows, "extract", force=True) == rows


def test_mixed_statuses() -> None:
    pending = _row("extract", StageStatus.PENDING, "p")
    done = _row("extract", StageStatus.DONE, "d")
    failed = _row("extract", StageStatus.FAILED, "f")
    rows = [pending, done, failed]

    assert select_pending(rows, "extract") == [pending, failed]
    assert select_pending(rows, "extract", only_failed=True) == [failed]
    assert select_pending(rows, "extract", force=True) == rows


def test_selection_is_per_stage() -> None:
    """同一列在不同階段的狀態互不干擾。"""
    row = CardRow(extract_status=StageStatus.DONE, image_status=StageStatus.PENDING)

    assert select_pending([row], "extract") == []
    assert select_pending([row], "image") == [row]


def test_force_and_only_failed_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="互斥"):
        select_pending([], "extract", force=True, only_failed=True)


def test_select_preserves_input_order() -> None:
    rows = [_row("extract", StageStatus.PENDING, f"c{i}") for i in range(5)]

    assert [r.card_id for r in select_pending(rows, "extract")] == [
        f"c{i}" for i in range(5)
    ]


# ── 狀態更新 ─────────────────────────────────────────────────────


def test_mark_done_sets_status_and_clears_error() -> None:
    row = CardRow(extract_status=StageStatus.FAILED, extract_error="舊錯誤")

    mark_done(row, "extract")

    assert row.extract_status is StageStatus.DONE
    assert row.extract_error == ""


def test_mark_failed_records_reason() -> None:
    row = CardRow()

    mark_failed(row, "extract", "card_id duplicated")

    assert row.extract_status is StageStatus.FAILED
    assert row.extract_error == "card_id duplicated"


def test_marking_one_stage_leaves_others_alone() -> None:
    row = CardRow()

    mark_failed(row, "audio_front", "timeout")

    assert row.audio_front_status is StageStatus.FAILED
    assert row.audio_back_status is StageStatus.PENDING
    assert row.audio_back_error == ""


def test_failed_then_rerun_succeeds() -> None:
    """狀態流轉：failed → 重跑成功 → done。"""
    row = CardRow()

    mark_failed(row, "image", "connection refused")
    mark_done(row, "image")

    assert row.image_status is StageStatus.DONE
    assert row.image_error == ""


# ── 統計 ─────────────────────────────────────────────────────────


def test_summarize_counts_every_stage() -> None:
    rows = [
        CardRow(ocr_status=StageStatus.DONE, extract_status=StageStatus.DONE),
        CardRow(ocr_status=StageStatus.DONE, extract_status=StageStatus.FAILED),
        CardRow(),
    ]

    summary = summarize(rows)

    assert set(summary) == set(STAGE_NAMES)
    assert summary["ocr"] == {
        StageStatus.PENDING: 1,
        StageStatus.DONE: 2,
        StageStatus.FAILED: 0,
    }
    assert summary["extract"][StageStatus.FAILED] == 1
    assert summary["image"][StageStatus.PENDING] == 3
    for stage in STAGE_NAMES:
        assert sum(summary[stage].values()) == 3


def test_summarize_on_empty_input() -> None:
    summary = summarize([])

    assert all(sum(counts.values()) == 0 for counts in summary.values())


def test_failed_rows_lists_only_that_stage() -> None:
    bad = CardRow(card_id="bad", extract_status=StageStatus.FAILED)
    rows = [CardRow(card_id="ok"), bad]

    assert failed_rows(rows, "extract") == [bad]
    assert failed_rows(rows, "image") == []
