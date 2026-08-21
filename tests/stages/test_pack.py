"""pack 階段單元測試（純本地檔案操作，無外部相依，AI 執行至通過）。"""

import csv
import io
import zipfile
from pathlib import Path

import pytest

from anki_deck_builder.exceptions import StageProcessingError
from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.stages import pack
from anki_deck_builder.stages.pack import CSV_NAME, MEDIA_DIRS
from anki_deck_builder.state import CardStore

pytestmark = pytest.mark.asyncio


def _card(card_id: str = "ja_n2_001", **overrides: object) -> CardRow:
    data: dict[str, object] = {
        "card_id": card_id,
        "deck": "日語::N2::動詞",
        "card_type": "basic",
        "front": "属する",
        "back": "屬於，歸於",
        "hint": "自サ",
        "example": "虎はネコ科に属する。\\n老虎屬於貓科。",
        "reading": "ぞくする",
        "image_prompt": "a tiger among cats, no text",
        "raw_text": "",
        "extract_status": StageStatus.DONE,
    }
    data.update(overrides)
    return CardRow(**data)


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "work" / "cards.csv")


def _zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def _zip_cards(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        raw = archive.read(CSV_NAME).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw, newline="")))


# ── 正常流程 ─────────────────────────────────────────────────────


async def test_writes_zip_with_expected_structure(store: CardStore, tmp_path: Path) -> None:
    await store.write([_card("a1"), _card("a2", front="続々")])
    output = tmp_path / "output" / "deck.zip"

    result = await pack(store, output)

    assert result.output == output
    assert result.card_count == 2
    assert output.exists()
    names = _zip_names(output)
    assert CSV_NAME in names
    assert set(MEDIA_DIRS) <= names


async def test_csv_contains_only_engine_fields(store: CardStore, tmp_path: Path) -> None:
    await store.write([_card()])
    output = tmp_path / "deck.zip"

    await pack(store, output)

    with zipfile.ZipFile(output) as archive:
        raw = archive.read(CSV_NAME).decode("utf-8-sig")
    header = next(csv.reader(io.StringIO(raw, newline="")))
    assert tuple(header) == CardRow.engine_field_order()
    assert len(header) == 23
    for dropped in ("raw_text", "reading", "image_prompt", "extract_status", "ocr_error"):
        assert dropped not in header


async def test_csv_keeps_bom_and_values(store: CardStore, tmp_path: Path) -> None:
    await store.write([_card()])
    output = tmp_path / "deck.zip"

    await pack(store, output)

    with zipfile.ZipFile(output) as archive:
        raw = archive.read(CSV_NAME)
    assert raw.startswith(b"\xef\xbb\xbf")

    card = _zip_cards(output)[0]
    assert card["card_id"] == "ja_n2_001"
    assert card["front"] == "属する"
    assert card["example"] == "虎はネコ科に属する。\\n老虎屬於貓科。"
    assert card["created_at"] == ""


async def test_source_rows_are_skipped(store: CardStore, tmp_path: Path) -> None:
    """extract 保留的 raw_text 列不是卡片，不進 ZIP。"""
    await store.write(
        [
            CardRow(raw_text="□属する\n□続々", extract_status=StageStatus.DONE),
            _card("a1"),
        ]
    )
    output = tmp_path / "deck.zip"

    result = await pack(store, output)

    assert (result.card_count, result.skipped_source_rows) == (1, 1)
    assert [c["card_id"] for c in _zip_cards(output)] == ["a1"]


async def test_creates_output_directory(store: CardStore, tmp_path: Path) -> None:
    await store.write([_card()])
    output = tmp_path / "deep" / "nested" / "deck.zip"

    await pack(store, output)

    assert output.exists()


# ── 媒體檔 ───────────────────────────────────────────────────────


async def test_media_files_are_included(store: CardStore, tmp_path: Path) -> None:
    work = store.path.parent
    (work / "media" / "img").mkdir(parents=True)
    (work / "media" / "audio").mkdir(parents=True)
    (work / "media" / "img" / "a1.png").write_bytes(b"PNG")
    (work / "media" / "audio" / "a1_front.mp3").write_bytes(b"MP3")
    await store.write(
        [
            _card(
                "a1",
                image_front="media/img/a1.png",
                audio_front="media/audio/a1_front.mp3",
            )
        ]
    )
    output = tmp_path / "deck.zip"

    result = await pack(store, output)

    assert result.media_count == 2
    names = _zip_names(output)
    assert "media/img/a1.png" in names
    assert "media/audio/a1_front.mp3" in names
    with zipfile.ZipFile(output) as archive:
        assert archive.read("media/img/a1.png") == b"PNG"


async def test_shared_media_file_is_stored_once(store: CardStore, tmp_path: Path) -> None:
    work = store.path.parent
    (work / "media" / "img").mkdir(parents=True)
    (work / "media" / "img" / "shared.png").write_bytes(b"PNG")
    await store.write(
        [
            _card("a1", image_front="media/img/shared.png"),
            _card("a2", image_front="media/img/shared.png"),
        ]
    )

    result = await pack(store, tmp_path / "deck.zip")

    assert result.media_count == 1


async def test_media_root_can_be_overridden(store: CardStore, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "media" / "img").mkdir(parents=True)
    (elsewhere / "media" / "img" / "a1.png").write_bytes(b"PNG")
    await store.write([_card("a1", image_front="media/img/a1.png")])

    result = await pack(store, tmp_path / "deck.zip", media_root=elsewhere)

    assert result.media_count == 1


async def test_missing_media_file_is_reported(store: CardStore, tmp_path: Path) -> None:
    await store.write([_card("a1", image_front="media/img/a1.png")])

    with pytest.raises(StageProcessingError) as excinfo:
        await pack(store, tmp_path / "deck.zip")

    assert "image_front 指向的檔案不存在" in str(excinfo.value)


# ── 完整性驗證 ───────────────────────────────────────────────────


@pytest.mark.parametrize("field", ["deck", "card_type", "front", "back"])
async def test_required_field_must_not_be_empty(
    store: CardStore, tmp_path: Path, field: str
) -> None:
    await store.write([_card(**{field: ""})])

    with pytest.raises(StageProcessingError, match=f"必填欄位 {field} 為空"):
        await pack(store, tmp_path / "deck.zip")


async def test_duplicate_card_id_is_reported(store: CardStore, tmp_path: Path) -> None:
    await store.write([_card("a1"), _card("a1", front="続々")])

    with pytest.raises(StageProcessingError, match="card_id 重複"):
        await pack(store, tmp_path / "deck.zip")


@pytest.mark.parametrize(
    "field", ["created_at", "last_reviewed", "interval", "ease_factor", "review_count"]
)
async def test_engine_managed_fields_must_be_empty(
    store: CardStore, tmp_path: Path, field: str
) -> None:
    await store.write([_card(**{field: "2026-08-21"})])

    with pytest.raises(StageProcessingError) as excinfo:
        await pack(store, tmp_path / "deck.zip")

    assert f"系統欄位 {field} 應留空" in str(excinfo.value)


async def test_empty_deck_is_rejected(store: CardStore, tmp_path: Path) -> None:
    await store.write([CardRow(raw_text="只有來源列", extract_status=StageStatus.DONE)])

    with pytest.raises(StageProcessingError, match="沒有任何卡片可打包"):
        await pack(store, tmp_path / "deck.zip")


async def test_all_problems_are_listed_at_once(store: CardStore, tmp_path: Path) -> None:
    """一次列出全部問題，不要修一個跑一次。"""
    await store.write([_card("a1", front=""), _card("a1", back=""), _card("a3", deck="")])

    with pytest.raises(StageProcessingError) as excinfo:
        await pack(store, tmp_path / "deck.zip")

    message = str(excinfo.value)
    assert "共 4 項問題" in message
    assert "必填欄位 front 為空" in message
    assert "必填欄位 back 為空" in message
    assert "必填欄位 deck 為空" in message
    assert "card_id 重複" in message


async def test_nothing_is_written_when_validation_fails(
    store: CardStore, tmp_path: Path
) -> None:
    await store.write([_card(front="")])
    output = tmp_path / "deck.zip"

    with pytest.raises(StageProcessingError):
        await pack(store, output)

    assert not output.exists()


# ── failed 列 ────────────────────────────────────────────────────


async def test_failed_rows_abort_by_default(store: CardStore, tmp_path: Path) -> None:
    await store.write(
        [
            _card("a1"),
            _card(
                "a2",
                extract_status=StageStatus.FAILED,
                extract_error="card_id duplicated",
            ),
        ]
    )

    with pytest.raises(StageProcessingError) as excinfo:
        await pack(store, tmp_path / "deck.zip")

    message = str(excinfo.value)
    assert "有 1 項失敗" in message
    assert "--allow-failed" in message
    assert "a2  extract: card_id duplicated" in message


async def test_allow_failed_proceeds(store: CardStore, tmp_path: Path) -> None:
    await store.write(
        [_card("a1"), _card("a2", image_status=StageStatus.FAILED, image_error="timeout")]
    )
    output = tmp_path / "deck.zip"

    result = await pack(store, output, allow_failed=True)

    assert result.card_count == 2
    assert [c["card_id"] for c in _zip_cards(output)] == ["a1", "a2"]


async def test_failed_source_row_is_reported_readably(
    store: CardStore, tmp_path: Path
) -> None:
    await store.write(
        [
            CardRow(
                raw_text="□属する",
                extract_status=StageStatus.FAILED,
                extract_error="read timeout",
            )
        ]
    )

    with pytest.raises(StageProcessingError, match=r"\(來源列\)  extract: read timeout"):
        await pack(store, tmp_path / "deck.zip")


async def test_multiple_failed_stages_are_all_listed(
    store: CardStore, tmp_path: Path
) -> None:
    await store.write(
        [
            _card(
                "a1",
                audio_front_status=StageStatus.FAILED,
                audio_front_error="tts down",
                audio_back_status=StageStatus.FAILED,
                audio_back_error="tts down",
            )
        ]
    )

    with pytest.raises(StageProcessingError) as excinfo:
        await pack(store, tmp_path / "deck.zip")

    message = str(excinfo.value)
    assert "有 2 項失敗" in message
    assert "audio_front" in message
    assert "audio_back" in message
