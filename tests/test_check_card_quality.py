"""`scripts/check-card-quality.py` 的 `punct` 檢查測試。

這一項的設計約束是**本專案支援任意領域與語言**（見 docs/project-overview.md）：
英文教材的 `...`、半形括號、半形句點都是**正確的**，誤報會在換教材時淹掉真正的
問題。因此表只收「任何語言都不該出現」的字元，省略號規則另外限定在含漢字的
文字裡才生效——這條約束就是本檔要釘住的東西。

`scripts/` 不是套件，以 importlib 由路徑載入（同 `test_migrate_to_image_scene.py`）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-card-quality.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_card_quality", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()


def _row(**values: str) -> dict[str, str]:
    row = {"card_id": "p1_001"}
    row.update(values)
    return row


def _findings(**values: str) -> list[str]:
    return check.check_punct([_row(**values)])


# ── 無條件為錯的字元 ─────────────────────────────────────────────


def test_the_radical_dot_is_reported() -> None:
    """`丶` U+4E36 是 CJK 部首「點」，不是標點——OCR 會把 `、` 誤判成它。"""
    found = _findings(back="（靠工作丶勞動）賺錢")
    assert len(found) == 1
    assert "丶→、" in found[0]


def test_the_maths_ellipsis_is_reported() -> None:
    """`⋯` U+22EF 是矩陣省略號，排版用的是 `…` U+2026。"""
    found = _findings(back="無價值；無聊；不下於⋯")
    assert len(found) == 1
    assert "⋯→…" in found[0]


def test_the_correct_ellipsis_is_left_alone() -> None:
    assert _findings(back="無價值；無聊；不下於…") == []


# ── 省略號：只在含漢字的文字裡才算錯 ─────────────────────────────


def test_ascii_dots_in_cjk_text_are_reported() -> None:
    found = _findings(back="限度；範圍；只要...就...")
    assert len(found) == 1
    assert "...→…" in found[0]


def test_middle_dots_in_cjk_text_are_reported() -> None:
    found = _findings(back="把···粘上；把···貼上")
    assert len(found) == 1
    assert "···→…" in found[0]


def test_repeated_runs_in_one_field_collapse_to_one_line() -> None:
    """`只要...就...` 是兩段相同寫法，報一行就夠——報兩行看起來像重複。"""
    assert len(_findings(back="只要...就...，也就是...")) == 1


def test_ascii_dots_in_english_material_are_not_reported() -> None:
    """**本檔最要緊的一條。** 英文教材的 `...` 是正確的，報了就是誤報。

    本專案的第一條目標是支援任意學習領域與語言，這條規則不能只對日文教材成立。
    """
    assert _findings(back="to put off; to delay... indefinitely") == []


def test_a_full_stop_run_in_english_is_not_reported() -> None:
    assert _findings(example="Wait... what?\nHe hesitated.") == []


def test_halfwidth_punctuation_is_never_reported() -> None:
    """半形括號與句點在英文裡正確，全形／半形的一致性不是本檢查的事。"""
    assert _findings(back="(a) the act of doing sth. properly") == []


# ── 涵蓋所有文字欄位 ─────────────────────────────────────────────


def test_every_text_field_is_scanned() -> None:
    rows = [_row(**{field: "ですが⋯"}) for field in check.TEXT_FIELDS]
    assert len(check.check_punct(rows)) == len(check.TEXT_FIELDS)


def test_a_clean_row_reports_nothing() -> None:
    assert _findings(front="稼ぐ", back="賺錢；爭取，獲得", example="点数を稼ぐ") == []


# ── 註冊 ─────────────────────────────────────────────────────────


def test_the_check_is_registered_and_titled() -> None:
    assert "punct" in check.CHECKS
    assert "punct" in check.TITLES
