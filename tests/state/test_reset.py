"""工作檔重置與清空的測試。

這是專案裡唯一會**刪東西**的模組，所以測試的重點不只是「有沒有刪掉」，
更是「**沒有**刪到不該刪的」：內容欄位、媒體目錄本身、子目錄裡的檔案。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.state import CardStore, backup_work, clear_rows, count_media, reset_stages


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "work" / "cards.csv")


def card(card_id: str = "a", **overrides: object) -> CardRow:
    values: dict[str, object] = {
        "card_id": card_id,
        "front": "属する",
        "image_front": "media/img/a.webp",
        "image_status": StageStatus.DONE,
        "audio_front_status": StageStatus.DONE,
        "audio_back_status": StageStatus.FAILED,
        "audio_back_error": "沒有文字",
        "extract_status": StageStatus.DONE,
    }
    values.update(overrides)
    return CardRow(**values)


async def seed(store: CardStore, *rows: CardRow) -> None:
    await store.write(list(rows))


def media_tree(root: Path, images: int = 2, sounds: int = 3) -> list[Path]:
    """建出 `media/img` 與 `media/audio`，回傳兩個目錄。"""
    img, audio = root / "media" / "img", root / "media" / "audio"
    img.mkdir(parents=True)
    audio.mkdir(parents=True)
    for i in range(images):
        (img / f"c{i}.webp").write_bytes(b"webp")
    for i in range(sounds):
        (audio / f"c{i}.mp3").write_bytes(b"mp3")
    return [img, audio]


# ── 備份 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backup_copies_the_work_file(store: CardStore) -> None:
    await seed(store, card())

    saved = await backup_work(store)

    assert saved is not None and saved.name.startswith("cards.csv.bak-")
    assert saved.read_bytes() == store.path.read_bytes()


@pytest.mark.asyncio
async def test_backup_of_a_missing_file_is_not_an_error(store: CardStore) -> None:
    """還沒建立工作檔就按重置——沒東西可備份，不該炸掉。"""
    assert await backup_work(store) is None


# ── 階段重置（非破壞性）──────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_stages_only_touches_those_stages(store: CardStore) -> None:
    await seed(store, card())

    result = await reset_stages(store, ["image"])

    row = (await store.read())[0]
    assert row.image_status is StageStatus.PENDING
    assert row.extract_status is StageStatus.DONE
    assert row.audio_front_status is StageStatus.DONE
    assert result.affected_rows == 1


@pytest.mark.asyncio
async def test_reset_stages_keeps_content_fields(store: CardStore) -> None:
    """重置狀態不等於丟掉成果——舊圖還在，重跑前仍看得到。"""
    await seed(store, card())

    await reset_stages(store, ["image"])

    row = (await store.read())[0]
    assert row.image_front == "media/img/a.webp"
    assert row.front == "属する"


@pytest.mark.asyncio
async def test_reset_stages_clears_the_stale_error(store: CardStore) -> None:
    await seed(store, card())

    await reset_stages(store, ["audio_back"])

    row = (await store.read())[0]
    assert row.audio_back_status is StageStatus.PENDING
    assert row.audio_back_error == ""


@pytest.mark.asyncio
async def test_reset_stages_counts_only_real_changes(store: CardStore) -> None:
    """本來就是 pending 的列不算「改動」，否則回報的數字會騙人。"""
    await seed(store, card("a"), card("b", image_status=StageStatus.PENDING))

    result = await reset_stages(store, ["image"])

    assert result.affected_rows == 1


@pytest.mark.asyncio
async def test_unknown_stage_names_the_legal_values(store: CardStore) -> None:
    """`audio` 不是階段——狀態層把它拆成 audio_front／audio_back。"""
    await seed(store, card())

    with pytest.raises(ValueError, match="audio_front"):
        await reset_stages(store, ["audio"])


@pytest.mark.asyncio
async def test_empty_stage_list_is_rejected(store: CardStore) -> None:
    await seed(store, card())

    with pytest.raises(ValueError, match="沒有指定"):
        await reset_stages(store, [])


@pytest.mark.asyncio
async def test_reset_stages_backs_up_first(store: CardStore) -> None:
    await seed(store, card())

    result = await reset_stages(store, ["image"])

    assert result.backup is not None and result.backup.is_file()


@pytest.mark.asyncio
async def test_backup_can_be_skipped(store: CardStore) -> None:
    await seed(store, card())

    result = await reset_stages(store, ["image"], backup=False)

    assert result.backup is None
    assert not list(store.path.parent.glob("*.bak-*"))


# ── 清空（破壞性）────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_rows_keeps_the_file_and_header(store: CardStore) -> None:
    """清空不是刪檔——工作檔還在，只是沒有列了，下一批教材直接寫得進去。"""
    await seed(store, card("a"), card("b"))

    result = await clear_rows(store)

    assert result.cleared_rows == 2
    assert store.exists() and await store.read() == []


@pytest.mark.asyncio
async def test_clear_rows_keeps_media_by_default(store: CardStore) -> None:
    """預設不刪媒體：清掉列之後圖與語音還在，反悔時只損失狀態。"""
    await seed(store, card())
    dirs = media_tree(store.path.parent)

    result = await clear_rows(store)

    assert result.removed_media == 0
    assert await count_media(dirs) == 5


@pytest.mark.asyncio
async def test_clear_rows_removes_media_when_asked(store: CardStore) -> None:
    await seed(store, card())
    dirs = media_tree(store.path.parent)

    result = await clear_rows(store, dirs)

    assert result.removed_media == 5
    assert await count_media(dirs) == 0


@pytest.mark.asyncio
async def test_media_directories_survive(store: CardStore) -> None:
    """只刪檔案不刪目錄——下一批教材寫進來時不必再建一次。"""
    await seed(store, card())
    dirs = media_tree(store.path.parent)

    await clear_rows(store, dirs)

    assert all(directory.is_dir() for directory in dirs)


@pytest.mark.asyncio
async def test_subdirectories_are_left_alone(store: CardStore) -> None:
    """不遞迴：使用者若把別的東西放進 media/，不該被順手掃掉。"""
    await seed(store, card())
    dirs = media_tree(store.path.parent)
    keep = dirs[0] / "原稿"
    keep.mkdir()
    (keep / "scan.png").write_bytes(b"png")

    result = await clear_rows(store, dirs)

    assert result.removed_media == 5
    assert (keep / "scan.png").is_file()


@pytest.mark.asyncio
async def test_clear_backs_up_before_deleting(store: CardStore) -> None:
    """破壞性操作的唯一退路。"""
    await seed(store, card("a"), card("b"))

    result = await clear_rows(store)

    assert result.backup is not None
    assert result.backup.read_text(encoding="utf-8-sig").count("\n") >= 3


@pytest.mark.asyncio
async def test_missing_media_directory_is_not_an_error(store: CardStore) -> None:
    """還沒生過圖就清空——目錄不存在，不該炸掉。"""
    await seed(store, card())

    result = await clear_rows(store, [store.path.parent / "media" / "img"])

    assert result.removed_media == 0
