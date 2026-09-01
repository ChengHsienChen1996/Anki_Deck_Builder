"""prompt 階段單元測試。

profile 選擇、產出檢查、語義保底與重試等純邏輯**可執行**；
LLM 呼叫一律以假 client 替換（依 Protocol 注入），不觸及任何真實服務。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from anki_deck_builder.exceptions import StageProcessingError
from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.stages import get_stage
from anki_deck_builder.stages.prompt import (
    MAX_PROMPT_CHARS,
    MIN_SEMANTIC_OVERLAP,
    PromptStage,
    check_prompt,
    clean,
    semantic_overlap,
)
from anki_deck_builder.state import CardStore

SCENE = "a tiger standing among a family of housecats, taxonomy mood"
FLUX = (
    "Anime. A tiger stands among a family of housecats, the size difference making it "
    "obvious which one does not belong. Soft cinematic light, muted colors."
)


class FakeLLMClient:
    """依序回傳指定的回應；不足時重複最後一個。"""

    def __init__(self, *replies: object, error: Exception | None = None) -> None:
        self.replies = list(replies) or [FLUX]
        self.error = error
        self.calls: list[tuple[str, object]] = []

    async def run_agent(self, agent_name: str, input_: object) -> object:
        self.calls.append((agent_name, input_))
        if self.error is not None:
            raise self.error
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def card(card_id: str = "ja_001", scene: str = SCENE, **kwargs) -> CardRow:
    return CardRow(card_id=card_id, front="属する", image_scene=scene, **kwargs)


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "work" / "cards.csv")


async def run_with(store: CardStore, rows: list[CardRow], client, **kwargs) -> tuple:
    await store.write(rows)
    stage = PromptStage(client, progress_stream=io.StringIO(), **kwargs)
    result = await stage.run(store)
    return result, await store.read()


# ── 註冊 ─────────────────────────────────────────────────────────


def test_registered_under_prompt() -> None:
    assert get_stage("prompt") is PromptStage


# ── clean ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reply",
    [FLUX, f"  {FLUX}  ", f'"{FLUX}"', f"```\n{FLUX}\n```", f"image_prompt:\n{FLUX}"],
)
def test_clean_unwraps_known_habits(reply: str) -> None:
    assert clean(reply) == FLUX


def test_clean_does_not_add_the_trigger_word() -> None:
    """補觸發詞就等於把它搬回程式，還會永久藏起「模型漏加」這個問題。"""
    assert clean("A tiger stands among housecats.") == "A tiger stands among housecats."


def test_clean_keeps_the_trailing_period() -> None:
    """自然語言 profile 的產出是句子，句號是它的一部分（與 scene 相反）。"""
    assert clean("A tiger stands among housecats.").endswith(".")


# ── 語義保底 ─────────────────────────────────────────────────────


def test_overlap_is_total_for_a_verbatim_copy() -> None:
    """SDXL profile 是「場景照抄 ＋ 接後綴」，重疊率必然 100%。"""
    suffixed = f"{SCENE}, cinematic lighting, muted color palette, no watermark"

    assert semantic_overlap(SCENE, suffixed) == 1.0


def test_overlap_survives_a_natural_language_rewrite() -> None:
    assert semantic_overlap(SCENE, FLUX) >= MIN_SEMANTIC_OVERLAP


@pytest.mark.parametrize(
    ("scene", "rewrite"),
    [
        pytest.param(
            "a small pile of coins next to a much larger overflowing pile, growth mood",
            "Anime. A small, neat pile of coins rests beside an enormous, overflowing "
            "mound of currency, illustrating the concept of rapid accumulation. "
            "Soft cinematic light, muted colors, gentle shadows.",
            id="comparatives-replaced-44pct",
        ),
        pytest.param(
            "a person gesturing towards a large group of diverse objects, "
            "encompassing everything mood",
            "Anime. A figure stands amidst an overwhelming collection of diverse "
            "objects, their hand outstretched as if presenting or indicating all of them. "
            "Soft cinematic light, muted colors, gentle shadows.",
            id="synonyms-throughout-20pct",
        ),
    ],
)
def test_faithful_rewrites_are_not_rejected(scene: str, rewrite: str) -> None:
    """兩次實測誤殺（2026-09-01）的回歸保護。

    兩者語義都完整保留，只是換了同義詞——散文改寫本來就會這樣。
    門檻若調回 0.3 或 0.5，這兩條就會紅。
    """
    assert semantic_overlap(scene, rewrite) >= MIN_SEMANTIC_OVERLAP


def test_overlap_collapses_when_the_subject_is_replaced() -> None:
    """換題材時重疊率接近 0——判別邊界很寬，門檻沒必要訂緊。"""
    other = "Anime. A lone astronaut floats above a quiet planet. Soft cinematic light."

    assert semantic_overlap(SCENE, other) < MIN_SEMANTIC_OVERLAP


def test_overlap_is_total_when_the_scene_has_no_content_words() -> None:
    """無從判斷就不擋——擋了只是把無法判斷的情形變成失敗。"""
    assert semantic_overlap("a b c", "anything at all") == 1.0


# ── check_prompt ─────────────────────────────────────────────────


def test_check_accepts_a_good_prompt() -> None:
    check_prompt(SCENE, FLUX)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("", "為空"),
        ("Anime. a cat", "太短"),
        ("a" * (MAX_PROMPT_CHARS + 1), "超過上限"),
        (f"{FLUX}\n這樣改寫比較自然。", "不是單獨一行"),
    ],
)
def test_check_rejects_bad_shapes(prompt: str, expected: str) -> None:
    with pytest.raises(StageProcessingError, match=expected):
        check_prompt(SCENE, prompt)


def test_check_rejects_a_prompt_that_changed_the_subject() -> None:
    other = "Anime. A lone astronaut floats above a quiet planet. Soft cinematic light."

    with pytest.raises(StageProcessingError, match="重疊") as excinfo:
        check_prompt(SCENE, other)

    # 訊息要帶上產出，否則判斷誤殺與否就得再跑一次
    assert "astronaut" in str(excinfo.value)


def test_check_does_not_require_the_trigger_word() -> None:
    """觸發詞刻意不做程式檢查——檢查它就得把它變回系統參數（使用者裁示）。"""
    check_prompt(SCENE, FLUX.removeprefix("Anime. "))


# ── profile 選擇 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uses_the_agent_from_settings(store: CardStore) -> None:
    client = FakeLLMClient(FLUX)

    await run_with(store, [card()], client, agent="ImagePromptSDXLAgent")

    assert client.calls[0][0] == "ImagePromptSDXLAgent"


@pytest.mark.asyncio
async def test_sends_only_the_scene(store: CardStore) -> None:
    """語法層的輸入就是語義層的產出——卡片的其他欄位與它無關。"""
    client = FakeLLMClient(FLUX)

    await run_with(store, [card(back="屬於，歸於")], client)

    assert client.calls[0][1] == SCENE


# ── 階段行為 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_writes_the_prompt_and_marks_done(store: CardStore) -> None:
    result, rows = await run_with(store, [card()], FakeLLMClient(FLUX))

    assert result.succeeded == 1
    assert rows[0].image_prompt == FLUX
    assert rows[0].prompt_status is StageStatus.DONE


@pytest.mark.asyncio
async def test_scene_is_left_untouched(store: CardStore) -> None:
    """換模型時只重生語法層——語義層一個字都不該動。"""
    _, rows = await run_with(store, [card()], FakeLLMClient(FLUX))

    assert rows[0].image_scene == SCENE


@pytest.mark.asyncio
async def test_source_row_is_skipped_not_failed(store: CardStore) -> None:
    client = FakeLLMClient(FLUX)

    result, rows = await run_with(store, [CardRow(raw_text="□属する", ocr_source_page=1)], client)

    assert client.calls == []
    assert result.failed == 0
    assert rows[0].prompt_status is StageStatus.DONE


@pytest.mark.asyncio
async def test_missing_scene_fails_with_a_pointer_to_the_scene_stage(store: CardStore) -> None:
    result, rows = await run_with(store, [card(scene="")], FakeLLMClient(FLUX))

    assert result.failed == 1
    assert "scene" in rows[0].prompt_error


@pytest.mark.asyncio
async def test_retries_once_then_succeeds(store: CardStore) -> None:
    client = FakeLLMClient("", FLUX)

    result, rows = await run_with(store, [card()], client)

    assert len(client.calls) == 2
    assert result.succeeded == 1
    assert rows[0].image_prompt == FLUX


@pytest.mark.asyncio
async def test_gives_up_after_the_second_attempt(store: CardStore) -> None:
    client = FakeLLMClient("short", "short")

    result, rows = await run_with(store, [card()], client)

    assert len(client.calls) == 2
    assert result.failed == 1
    assert rows[0].image_prompt == ""


@pytest.mark.asyncio
async def test_client_error_becomes_a_row_failure(store: CardStore) -> None:
    result, rows = await run_with(store, [card()], FakeLLMClient(error=RuntimeError("boom")))

    assert result.failed == 1
    assert "boom" in rows[0].prompt_error


@pytest.mark.asyncio
async def test_non_string_reply_points_at_output_schema(store: CardStore) -> None:
    result, rows = await run_with(store, [card()], FakeLLMClient(object()))

    assert result.failed == 1
    assert "output_schema" in rows[0].prompt_error
