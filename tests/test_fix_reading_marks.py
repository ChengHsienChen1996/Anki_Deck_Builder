"""`scripts/fix-reading-marks.py` 的測試。

腳本會改寫使用者工作檔的 `reading` 與 `tts_front_text`，判定條件放寬一格就會
把多音字改壞（`清く` 的 `きよく` 被改成 `きょく`），而那種錯誤不會有任何訊號——
音檔照樣生得出來，只是唸錯。因此雖然它是手動工具仍然測。

`scripts/` 不是套件，以 importlib 由路徑載入（同
`test_migrate_to_image_scene.py`）。
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fix-reading-marks.py"
ENCODING = "utf-8-sig"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fix_reading_marks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fix = _load()


# ── flatten：骨架比對 ─────────────────────────────────────────────


def test_flatten_erases_the_dakuten() -> None:
    assert fix.flatten("ぎょぎょう") == fix.flatten("きょきょう")


def test_flatten_erases_the_small_kana() -> None:
    assert fix.flatten("きゅう") == fix.flatten("きゆう")


def test_flatten_erases_both_at_once() -> None:
    """`きよたい` → `きょだい` 同時缺濁點與小寫假名，兩者骨架仍相同。"""
    assert fix.flatten("きょだい") == fix.flatten("きよたい")


def test_flatten_keeps_genuinely_different_readings_apart() -> None:
    """骨架不同就不是 marks 問題，條件必須擋下來。"""
    assert fix.flatten("こうほ") != fix.flatten("こうぼう")


# ── 多讀音的分隔符 ───────────────────────────────────────────────


def test_split_reading_without_separator() -> None:
    assert fix._split_reading("きゆう") == ("きゆう", "", "")


def test_split_reading_keeps_the_rest_verbatim() -> None:
    """分隔符後面的部分要原樣接回去，不能整欄覆蓋。"""
    head, sep, rest = fix._split_reading("きゆう／キュウ")
    assert (head, sep, rest) == ("きゆう", "／", "キュウ")


# ── 端到端：真的跑一次腳本 ───────────────────────────────────────

pytest.importorskip("sudachipy", reason="marks 修正需要形態素分析器（uv sync --all-extras）")

HEADER = ["card_id", "front", "reading", "tts_front_text",
          "audio_front_status", "audio_front_error",
          "audio_back_status", "audio_back_error"]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding=ENCODING, newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in HEADER})


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding=ENCODING, newline="") as fh:
        return list(csv.DictReader(fh))


def _run(path: Path, *flags: str) -> int:
    argv = ["fix-reading-marks.py", str(path), *flags]
    original, sys.argv = sys.argv, argv
    try:
        return fix.main()
    finally:
        sys.argv = original


def test_missing_small_kana_is_restored(tmp_path: Path) -> None:
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": "p1_001", "front": "旧", "reading": "きゆう",
                       "tts_front_text": "きゆう", "audio_front_status": "pending"}])
    assert _run(work, "--apply") == 0
    row = _read_csv(work)[0]
    assert row["reading"] == "きゅう"
    assert row["tts_front_text"] == "きゅう"


def test_missing_dakuten_is_restored(tmp_path: Path) -> None:
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": "p1_001", "front": "漁業", "reading": "きょきょう",
                       "tts_front_text": "きょきょう", "audio_front_status": "pending"}])
    assert _run(work, "--apply") == 0
    assert _read_csv(work)[0]["reading"] == "ぎょぎょう"


def test_a_spurious_small_kana_is_also_removed(tmp_path: Path) -> None:
    """方向是雙向的——`器用` 卡片多了小寫假名，分析器的 `きよう` 才對。"""
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": "p1_001", "front": "器用", "reading": "きょう",
                       "tts_front_text": "きょう", "audio_front_status": "pending"}])
    assert _run(work, "--apply") == 0
    assert _read_csv(work)[0]["reading"] == "きよう"


def test_a_correct_reading_is_left_alone(tmp_path: Path) -> None:
    """`清く` 的 `きよく` 是對的。分析器對它也給 `きよく`，不該被動到。

    這是整支腳本最要緊的一條：改壞了不會有任何訊號。
    """
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": "p1_001", "front": "清く", "reading": "きよく",
                       "tts_front_text": "きよく", "audio_front_status": "done"}])
    assert _run(work, "--apply") == 0
    row = _read_csv(work)[0]
    assert row["reading"] == "きよく"
    assert row["audio_front_status"] == "done"


def test_a_wholly_different_reading_is_left_alone(tmp_path: Path) -> None:
    """骨架不同代表不是 marks 問題（可能是多音字選錯），本腳本不碰。"""
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": "p1_001", "front": "候補", "reading": "そうろうほ",
                       "tts_front_text": "そうろうほ", "audio_front_status": "pending"}])
    assert _run(work, "--apply") == 0
    assert _read_csv(work)[0]["reading"] == "そうろうほ"


def test_preview_writes_nothing(tmp_path: Path) -> None:
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": "p1_001", "front": "旧", "reading": "きゆう",
                       "tts_front_text": "きゆう", "audio_front_status": "done"}])
    before = work.read_bytes()
    assert _run(work) == 0
    assert work.read_bytes() == before


def test_finished_front_audio_is_queued_for_regeneration(tmp_path: Path) -> None:
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": "p1_001", "front": "旧", "reading": "きゆう",
                       "tts_front_text": "きゆう", "audio_front_status": "done",
                       "audio_front_error": "舊訊息", "audio_back_status": "done"}])
    assert _run(work, "--apply") == 0
    row = _read_csv(work)[0]
    assert row["audio_front_status"] == "pending"
    assert row["audio_front_error"] == ""
    # back 側唸的是例句，與詞條讀音無關
    assert row["audio_back_status"] == "done"


def test_a_diverging_tts_field_is_not_overwritten(tmp_path: Path) -> None:
    """`tts_front_text` 另有來歷時要判斷哪個對，交給 `fields` 檢查，不猜。"""
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": "p1_001", "front": "旧", "reading": "きゆう",
                       "tts_front_text": "ふるい", "audio_front_status": "done"}])
    assert _run(work, "--apply") == 0
    row = _read_csv(work)[0]
    assert row["reading"] == "きゅう"
    assert row["tts_front_text"] == "ふるい"
    # 唸的文字沒變，音檔就不必重生
    assert row["audio_front_status"] == "done"


def test_the_rest_of_a_multi_reading_field_survives(tmp_path: Path) -> None:
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": "p1_001", "front": "旧", "reading": "きゆう／キュウ",
                       "tts_front_text": "きゆう", "audio_front_status": "pending"}])
    assert _run(work, "--apply") == 0
    assert _read_csv(work)[0]["reading"] == "きゅう／キュウ"


def test_skipped_cards_are_left_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_CARD_SKIP` 裡的卡片詞目本身是壞的，自動改讀音只會把問題藏起來。

    表目前是空的（唯一進過的 `p38_002` 已修正源頭），所以測試自己注入一筆——
    要驗的是**機制**還在，不是那張表現在裝了誰。
    """
    card_id = "p38_002"
    monkeypatch.setitem(fix._CARD_SKIP, card_id, "詞目本身壞掉，非 marks 問題")
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": card_id, "front": "これ腰", "reading": "これごし",
                       "tts_front_text": "これごし", "audio_front_status": "done"}])
    assert _run(work, "--apply") == 0
    row = _read_csv(work)[0]
    assert row["reading"] == "これごし"
    assert row["audio_front_status"] == "done"


def test_running_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    work = tmp_path / "cards.csv"
    _write_csv(work, [{"card_id": "p1_001", "front": "旧", "reading": "きゆう",
                       "tts_front_text": "きゆう", "audio_front_status": "done"}])
    assert _run(work, "--apply") == 0
    after_first = work.read_bytes()
    assert _run(work, "--apply") == 0
    assert work.read_bytes() == after_first


def test_a_missing_column_is_refused(tmp_path: Path) -> None:
    """少欄位就停手——半套寫回比不寫更糟。"""
    work = tmp_path / "cards.csv"
    with work.open("w", encoding=ENCODING, newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["card_id", "front", "reading"])
        writer.writerow(["p1_001", "旧", "きゆう"])
    assert _run(work, "--apply") == 1
