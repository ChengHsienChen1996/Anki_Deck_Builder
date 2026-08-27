"""StageStatus 單元測試。"""

import pytest

from anki_deck_builder.schemas import StageStatus


def test_values_match_spec() -> None:
    assert StageStatus.PENDING.value == "pending"
    assert StageStatus.DONE.value == "done"
    assert StageStatus.FAILED.value == "failed"
    assert [s.value for s in StageStatus] == ["pending", "done", "failed"]


def test_str_gives_plain_value() -> None:
    """寫入 CSV 時直接用 str()，不能得到 'StageStatus.PENDING'。"""
    assert str(StageStatus.PENDING) == "pending"
    assert f"{StageStatus.FAILED}" == "failed"


def test_compares_equal_to_plain_string() -> None:
    assert StageStatus.DONE == "done"


def test_constructed_from_string() -> None:
    assert StageStatus("failed") is StageStatus.FAILED


def test_unknown_value_rejected() -> None:
    with pytest.raises(ValueError, match="skipped"):
        StageStatus("skipped")
