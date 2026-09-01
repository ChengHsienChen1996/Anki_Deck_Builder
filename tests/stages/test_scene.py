"""scene 階段單元測試。

狀態流轉、跳過非卡片列、產出檢查與重試等純邏輯**可執行**；
LLM 呼叫一律以假 client 替換（依 Protocol 注入），不觸及任何真實服務。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from anki_deck_builder.exceptions import StageProcessingError
from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.stages import get_stage
from anki_deck_builder.stages.scene import (
    MAX_SCENE_CHARS,
    SceneStage,
    build_input,
    check_scene,
    clean,
)
from anki_deck_builder.state import CardStore

GOOD = "a tiger standing among a family of housecats, taxonomy mood"


class FakeLLMClient:
    """依序回傳指定的回應；不足時重複最後一個。"""

    def __init__(self, *replies: object, error: Exception | None = None) -> None:
        self.replies = list(replies) or [GOOD]
        self.error = error
        self.calls: list[tuple[str, object]] = []

    async def run_agent(self, agent_name: str, input_: object) -> object:
        self.calls.append((agent_name, input_))
        if self.error is not None:
            raise self.error
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[index]


def card(card_id: str = "ja_001", **kwargs) -> CardRow:
    values: dict[str, object] = {"front": "属する", "back": "屬於，歸於"}
    values.update(kwargs)
    return CardRow(card_id=card_id, **values)


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "work" / "cards.csv")


async def run_with(store: CardStore, rows: list[CardRow], client) -> tuple:
    await store.write(rows)
    stage = SceneStage(client, progress_stream=io.StringIO())
    result = await stage.run(store)
    return result, await store.read()


# ── 註冊 ─────────────────────────────────────────────────────────


def test_registered_under_scene() -> None:
    assert get_stage("scene") is SceneStage


# ── clean：拆包裝 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reply",
    [
        GOOD,
        f"  {GOOD}  ",
        f'"{GOOD}"',
        f"'{GOOD}'",
        f"```\n{GOOD}\n```",
        f"{GOOD}.",
        f"image_scene:\n{GOOD}",
        f"場景：\n{GOOD}",
    ],
)
def test_clean_unwraps_known_habits(reply: str) -> None:
    """引號、code fence、標籤行、句號都是這個本地模型的已知習慣，拆掉不改語義。"""
    assert clean(reply) == GOOD


def test_clean_keeps_extra_lines_for_the_check_to_reject() -> None:
    """多行代表模型多寫了說明。不猜哪一行是答案——原樣交給檢查擋下。"""
    cleaned = clean(f"{GOOD}\n這個場景可以讓人聯想到分類關係。")

    assert "\n" in cleaned


def test_clean_returns_empty_for_label_only_reply() -> None:
    assert clean("場景：") == ""


# ── check_scene：產出檢查 ────────────────────────────────────────


def test_check_accepts_a_good_scene() -> None:
    check_scene(GOOD)


@pytest.mark.parametrize(
    ("scene", "expected"),
    [
        ("", "為空"),
        ("a cat", "太短"),
        ("a" * (MAX_SCENE_CHARS + 1), "超過上限"),
        (f"{GOOD}\n說明", "不是單獨一行"),
    ],
)
def test_check_rejects_bad_shapes(scene: str, expected: str) -> None:
    with pytest.raises(StageProcessingError, match=expected):
        check_scene(scene)


@pytest.mark.parametrize(
    "leak",
    [
        f"{GOOD}, cinematic lighting",
        f"{GOOD}, muted color palette",
        f"{GOOD}, no watermark",
        f"{GOOD}, masterpiece, best quality",
    ],
)
def test_check_rejects_syntax_layer_leaking_into_the_scene(leak: str) -> None:
    """語義層混進風格詞，換模型時會被重複帶進新 profile——這是層次被打破。"""
    with pytest.raises(StageProcessingError, match="風格詞"):
        check_scene(leak)


def test_check_allows_no_text_wording_in_a_scene() -> None:
    """`no text` 不列入禁用詞：`a blank sign with no text` 是合法的場景描述。

    收進去會誤殺，而它擋不住的東西由 image_scene.md 的道具表治本。
    """
    check_scene("an empty classroom desk beside a blank board with no text on it")


# ── build_input ──────────────────────────────────────────────────


def test_input_carries_the_semantic_fields() -> None:
    text = build_input(card(example="虎はネコ科に属する。\\n老虎屬於貓科。", hint="自サ"))

    assert "属する" in text and "屬於，歸於" in text
    assert "虎はネコ科に属する。" in text
    assert "自サ" in text


def test_input_unescapes_the_example_separator() -> None:
    """example 以字面 \\n 分隔原文與譯文，還原成換行才好讀。"""
    text = build_input(card(example="原文。\\n譯文。"))

    assert "\\n" not in text
    assert "原文。\n譯文。" in text


def test_input_omits_fields_that_do_not_affect_the_picture() -> None:
    text = build_input(card(deck="日語::N2::動詞", tags="n2 verb", difficulty="4"))

    assert "日語::N2::動詞" not in text
    assert "n2 verb" not in text


# ── 階段行為 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_writes_the_scene_and_marks_done(store: CardStore) -> None:
    result, rows = await run_with(store, [card()], FakeLLMClient(GOOD))

    assert result.succeeded == 1
    assert rows[0].image_scene == GOOD
    assert rows[0].scene_status is StageStatus.DONE


@pytest.mark.asyncio
async def test_uses_the_scene_agent(store: CardStore) -> None:
    client = FakeLLMClient(GOOD)

    await run_with(store, [card()], client)

    assert client.calls[0][0] == "SceneAgent"


@pytest.mark.asyncio
async def test_source_row_is_skipped_not_failed(store: CardStore) -> None:
    """ocr／extract 留下的來源列不是卡片，不該有場景，也不該算失敗。"""
    client = FakeLLMClient(GOOD)

    result, rows = await run_with(store, [CardRow(raw_text="□属する", ocr_source_page=1)], client)

    assert client.calls == []
    assert result.failed == 0
    assert rows[0].scene_status is StageStatus.DONE
    assert rows[0].image_scene == ""


@pytest.mark.asyncio
async def test_card_without_front_fails_with_a_pointer_to_extract(store: CardStore) -> None:
    result, rows = await run_with(store, [card(front="")], FakeLLMClient(GOOD))

    assert result.failed == 1
    assert rows[0].scene_status is StageStatus.FAILED
    assert "extract" in rows[0].scene_error


@pytest.mark.asyncio
async def test_retries_once_then_succeeds(store: CardStore) -> None:
    client = FakeLLMClient("", GOOD)

    result, rows = await run_with(store, [card()], client)

    assert len(client.calls) == 2
    assert result.succeeded == 1
    assert rows[0].image_scene == GOOD


@pytest.mark.asyncio
async def test_gives_up_after_the_second_attempt(store: CardStore) -> None:
    client = FakeLLMClient("bad", "bad")

    result, rows = await run_with(store, [card()], client)

    assert len(client.calls) == 2
    assert result.failed == 1
    assert rows[0].image_scene == ""


@pytest.mark.asyncio
async def test_client_error_becomes_a_row_failure(store: CardStore) -> None:
    """約束 3：單列失敗只寫進該列，不中斷整批。"""
    result, rows = await run_with(store, [card()], FakeLLMClient(error=RuntimeError("boom")))

    assert result.failed == 1
    assert "boom" in rows[0].scene_error


@pytest.mark.asyncio
async def test_non_string_reply_points_at_output_schema(store: CardStore) -> None:
    result, rows = await run_with(store, [card()], FakeLLMClient(object()))

    assert result.failed == 1
    assert "output_schema" in rows[0].scene_error


@pytest.mark.asyncio
async def test_one_failure_does_not_stop_the_others(store: CardStore) -> None:
    client = FakeLLMClient(GOOD)

    result, rows = await run_with(store, [card("a"), card("b", front=""), card("c")], client)

    assert (result.succeeded, result.failed) == (2, 1)
    assert [row.image_scene for row in rows] == [GOOD, "", GOOD]
