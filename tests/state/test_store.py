"""CardStore 單元測試（非付費模組，AI 執行至通過）。

重點：原子寫入。模擬中斷後原檔必須完整可讀。
"""

import csv
import os
from pathlib import Path

import pytest

from anki_deck_builder.exceptions import WorkFileError
from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.state import CardStore

pytestmark = pytest.mark.asyncio


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "cards.csv")


def _sample_rows() -> list[CardRow]:
    return [
        CardRow(
            card_id="ja_n2_001",
            deck="日語::N2::動詞",
            front="属する",
            back="屬於，歸於；隸屬，附屬",
            example="虎はネコ科に属する。\\n老虎屬於貓科。",
            note='含逗號, 引號" 與 \\n 字面值',
            ocr_source_page=1,
            reading="ぞくする",
            ocr_status=StageStatus.DONE,
            extract_status=StageStatus.DONE,
        ),
        CardRow(
            card_id="ja_n2_002",
            front="装置",
            ocr_source_page=2,
            extract_status=StageStatus.FAILED,
            extract_error="card_id duplicated",
        ),
    ]


# ── 建檔與基本讀寫 ───────────────────────────────────────────────


async def test_create_empty_writes_header_only(store: CardStore) -> None:
    await store.create_empty()

    assert store.exists()
    assert await store.read() == []
    with store.path.open(encoding="utf-8-sig", newline="") as fh:
        assert tuple(next(csv.reader(fh))) == CardRow.field_order()


async def test_create_empty_does_not_clobber_existing(store: CardStore) -> None:
    await store.write(_sample_rows())

    await store.create_empty()

    assert len(await store.read()) == 2


async def test_write_creates_parent_directories(tmp_path: Path) -> None:
    store = CardStore(tmp_path / "deep" / "nested" / "cards.csv")

    await store.write([])

    assert store.path.exists()


async def test_round_trip_preserves_every_field(store: CardStore) -> None:
    rows = _sample_rows()

    await store.write(rows)

    assert await store.read() == rows


async def test_round_trip_of_empty_file(store: CardStore) -> None:
    await store.write([])
    assert await store.read() == []


async def test_special_characters_survive(store: CardStore) -> None:
    row = CardRow(card_id="x", back='逗號, 引號" 分號；\\n 字面值')

    await store.write([row])

    assert (await store.read())[0].back == row.back


# ── 檔案格式 ─────────────────────────────────────────────────────


async def test_file_is_utf8_with_bom_and_crlf(store: CardStore) -> None:
    await store.write(_sample_rows())

    raw = store.path.read_bytes()

    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.count(b"\r\n") == 3  # 表頭 + 兩列
    assert "属する".encode() in raw


async def test_column_order_follows_field_order(store: CardStore) -> None:
    await store.write(_sample_rows())

    with store.path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))

    assert tuple(rows[0]) == CardRow.field_order()
    assert len(rows[1]) == 39


async def test_reads_golden_fixture(expected_cards_csv: Path) -> None:
    """對齊樣板由外部產生，格式必須與本模組相容。"""
    rows = await CardStore(expected_cards_csv).read()

    assert len(rows) == 16
    assert rows[0].card_id == "fx_n2_001"
    assert rows[0].ocr_source_page == 1


# ── 錯誤處理 ─────────────────────────────────────────────────────


async def test_read_missing_file_raises(store: CardStore) -> None:
    with pytest.raises(WorkFileError, match="不存在"):
        await store.read()


async def test_read_empty_file_raises(store: CardStore) -> None:
    store.path.write_text("", encoding="utf-8")

    with pytest.raises(WorkFileError, match="沒有表頭"):
        await store.read()


async def test_header_mismatch_raises_with_diff(store: CardStore) -> None:
    store.path.write_text("card_id,front,back\r\na,b,c\r\n", encoding="utf-8-sig")

    with pytest.raises(WorkFileError) as excinfo:
        await store.read()

    message = str(excinfo.value)
    assert "表頭與 CardRow.field_order() 不一致" in message
    assert "缺少" in message


async def test_short_row_raises(store: CardStore) -> None:
    header = ",".join(CardRow.field_order())
    store.path.write_text(f"{header}\r\na,b,c\r\n", encoding="utf-8-sig")

    with pytest.raises(WorkFileError, match="第 2 行"):
        await store.read()


# ── 原子寫入 ─────────────────────────────────────────────────────


async def test_no_temp_file_left_after_success(store: CardStore) -> None:
    await store.write(_sample_rows())

    assert not store.tmp_path.exists()


async def test_interrupted_rename_leaves_original_intact(
    store: CardStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模擬 rename 當下被打斷：原檔必須是完整的舊內容。"""
    await store.write(_sample_rows())
    before = store.path.read_bytes()

    def boom(src: object, dst: object) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(WorkFileError, match="寫入中間 CSV 失敗"):
        await store.write([CardRow(card_id="new")])

    assert store.path.read_bytes() == before
    assert len(await store.read()) == 2
    assert not store.tmp_path.exists()


async def test_interrupted_write_never_touches_original(
    store: CardStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """寫暫存檔階段就失敗時，原檔連碰都不該被碰到。"""
    await store.write(_sample_rows())
    before = store.path.read_bytes()

    real_open = __import__("aiofiles").open

    def failing_open(path: object, *args: object, **kwargs: object) -> object:
        if str(path).endswith(".tmp"):
            raise OSError("disk full")
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("anki_deck_builder.state.store.aiofiles.open", failing_open)

    with pytest.raises(WorkFileError):
        await store.write([CardRow(card_id="new")])

    assert store.path.read_bytes() == before


async def test_write_goes_through_temp_file(
    store: CardStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """確認實作真的走 tmp + rename，而非直接覆寫。"""
    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src: object, dst: object) -> None:
        seen.append((str(src), str(dst)))
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", spy)

    await store.write(_sample_rows())

    assert seen == [(str(store.tmp_path), str(store.path))]
