"""extract 階段測試。

**分兩類**（依 docs/architecture.md〈專案獨有規則〉第 2 條）：

- 純邏輯（任務參數組裝、`card_id` 唯一性、狀態更新、併發設定、agent 選擇分支）
  以注入的假 client 驗證，**可執行**。
- 真正呼叫 LLM 的案例標記 `manual`，**AI 不執行**，需人工跑：

      uv run pytest -m manual tests/stages/test_extract.py -v
"""

from pathlib import Path

import pytest
from pydantic import BaseModel

from anki_deck_builder.clients.protocols import AgentInput
from anki_deck_builder.config import load_settings
from anki_deck_builder.exceptions import StageProcessingError
from anki_deck_builder.schemas import (
    CardRow,
    ExtractedCard,
    ExtractOutput,
    StageStatus,
)
from anki_deck_builder.stages import get_stage
from anki_deck_builder.stages.extract import TEXT_AGENT, VISION_AGENT, ExtractStage
from anki_deck_builder.state import CardStore


class FakeLLMClient:
    """依 Protocol 注入的假 client，攔在任何真實呼叫之前。"""

    def __init__(self, *, cards_per_call: int = 1, error: Exception | None = None) -> None:
        self.cards_per_call = cards_per_call
        self.error = error
        self.calls: list[tuple[str, AgentInput]] = []
        self.responses: list[BaseModel] | None = None
        self.max_concurrent = 0
        self._active = 0

    async def run_agent(self, agent_name: str, input_: AgentInput) -> BaseModel:
        self._active += 1
        self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            self.calls.append((agent_name, input_))
            if self.error is not None:
                raise self.error
            if self.responses is not None:
                return self.responses[len(self.calls) - 1]
            return _output(
                [f"c{len(self.calls)}_{i}" for i in range(1, self.cards_per_call + 1)]
            )
        finally:
            self._active -= 1


def _card(card_id: str, **overrides: object) -> ExtractedCard:
    data: dict[str, object] = {
        "card_id": card_id,
        "deck": "日語::N2::動詞",
        "front": "属する",
        "back": "屬於，歸於",
        "hint": "自サ",
        "example": "虎はネコ科に属する。\\n老虎屬於貓科。",
        "reading": "ぞくする",
        "image_prompt": "a tiger among cats, no text, no letters, no watermark",
        "tts_front_text": "ぞくする",
        "tts_back_text": "虎はネコ科に属する。",
        "difficulty": 3,
    }
    data.update(overrides)
    return ExtractedCard(**data)


def _output(card_ids: list[str]) -> ExtractOutput:
    return ExtractOutput(cards=[_card(cid) for cid in card_ids])


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "cards.csv")


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("YAML_SETTINGS_FILE", "agents.yaml")
    monkeypatch.setenv("GLOBAL_CONCURRENCY", "3")
    monkeypatch.delenv("INGEST_MODE", raising=False)
    return load_settings(env_file=None)


# ── 註冊與設定 ───────────────────────────────────────────────────


def test_registered_as_extract() -> None:
    assert get_stage("extract") is ExtractStage
    assert ExtractStage.name == "extract"


def test_concurrency_comes_from_global_concurrency(settings) -> None:
    assert ExtractStage(FakeLLMClient(), settings=settings).concurrency == 3


def test_concurrency_defaults_to_one_without_settings() -> None:
    assert ExtractStage(FakeLLMClient()).concurrency == 1


# ── 任務參數組裝 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_input_carries_all_task_parameters() -> None:
    stage = ExtractStage(
        FakeLLMClient(),
        deck_name="日語::N2",
        domain="日語 N2 單字",
        source="單字書 p.333",
        card_id_prefix="ja_n2",
    )
    row = CardRow(raw_text="□属する", ocr_source_page=1)

    agent_name, input_ = await stage._build_request(row)

    assert agent_name == TEXT_AGENT
    header, body = input_.split("\n\n", 1)
    assert body == "□属する"
    assert "領域: 日語 N2 單字" in header
    assert "牌組前綴: 日語::N2" in header
    assert "來源: 單字書 p.333" in header


@pytest.mark.asyncio
async def test_card_id_prefix_includes_page_number() -> None:
    """每頁是獨立一次呼叫，不帶頁碼會讓不同頁的序號互撞。"""
    stage = ExtractStage(FakeLLMClient(), card_id_prefix="ja_n2")

    _, page_one = await stage._build_request(CardRow(raw_text="x", ocr_source_page=1))
    _, page_two = await stage._build_request(CardRow(raw_text="x", ocr_source_page=2))

    assert "卡片ID前綴: ja_n2_p1" in page_one
    assert "卡片ID前綴: ja_n2_p2" in page_two


@pytest.mark.asyncio
async def test_input_omits_unset_parameters() -> None:
    stage = ExtractStage(FakeLLMClient(), deck_name="日語::N2")

    _, input_ = await stage._build_request(CardRow(raw_text="□属する"))

    assert input_ == "牌組前綴: 日語::N2\n\n□属する"
    assert "領域" not in input_
    assert "來源" not in input_


@pytest.mark.asyncio
async def test_input_is_raw_text_when_nothing_specified() -> None:
    stage = ExtractStage(FakeLLMClient())

    _, input_ = await stage._build_request(CardRow(raw_text="□属する"))

    assert input_ == "□属する"


# ── agent 選擇分支（Phase 2 的接點）────────────────────────────────


@pytest.mark.asyncio
async def test_text_mode_uses_extract_agent(settings) -> None:
    stage = ExtractStage(FakeLLMClient(), settings=settings)

    agent_name, _ = await stage._build_request(CardRow(raw_text="x"))

    assert agent_name == TEXT_AGENT


def _vision_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("YAML_SETTINGS_FILE", "agents.yaml")
    monkeypatch.setenv("INGEST_MODE", "vision_direct")
    return load_settings(env_file=None)


def _image_row(tmp_path: Path, page: int = 1) -> CardRow:
    """vision_direct 下 ocr 階段留下的列：有影像來源、raw_text 留空。"""
    from PIL import Image

    path = tmp_path / f"page{page}.jpg"
    Image.new("RGB", (40, 30), (200, 200, 200)).save(path)
    return CardRow(source=str(path), ocr_source_page=page)


@pytest.mark.asyncio
async def test_vision_direct_uses_vision_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = ExtractStage(FakeLLMClient(), settings=_vision_settings(monkeypatch))

    agent_name, input_ = await stage._build_request(_image_row(tmp_path))

    assert agent_name == VISION_AGENT
    assert isinstance(input_, list)


@pytest.mark.asyncio
async def test_vision_input_carries_image_and_task_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """兩條路徑的任務參數必須一致，否則 card_id 前綴等設定只在一條路上生效。"""
    stage = ExtractStage(
        FakeLLMClient(),
        settings=_vision_settings(monkeypatch),
        deck_name="日語::N2",
        card_id_prefix="ja_n2",
    )

    _, input_ = await stage._build_request(_image_row(tmp_path, page=2))

    image_block = input_[0]["content"][0]
    assert image_block["type"] == "input_image"
    assert image_block["image_url"].startswith("data:image/jpeg;base64,")
    assert "牌組前綴: 日語::N2" in input_[1]["content"]
    assert "卡片ID前綴: ja_n2_p2" in input_[1]["content"]


@pytest.mark.asyncio
async def test_raw_text_wins_even_in_vision_direct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已經有文字就別再送影像——省一次 vision 推理，結果也更穩。"""
    stage = ExtractStage(FakeLLMClient(), settings=_vision_settings(monkeypatch))
    row = _image_row(tmp_path)
    row.raw_text = "□属する"

    agent_name, input_ = await stage._build_request(row)

    assert agent_name == TEXT_AGENT
    assert isinstance(input_, str)


@pytest.mark.asyncio
async def test_two_stage_does_not_fall_back_to_vision(
    store: CardStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """two_stage 下空的 raw_text 代表 OCR 失敗，該讓它失敗，不是偷偷改走影像路徑。"""
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("YAML_SETTINGS_FILE", "agents.yaml")
    monkeypatch.setenv("INGEST_MODE", "two_stage")
    settings = load_settings(env_file=None)
    await store.write([_image_row(tmp_path)])

    result = await ExtractStage(FakeLLMClient(), settings=settings).run(store)

    assert result.failed == 1
    assert "raw_text" in (await store.read())[0].extract_error


@pytest.mark.asyncio
async def test_vision_row_without_source_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    stage = ExtractStage(FakeLLMClient(), settings=_vision_settings(monkeypatch))

    with pytest.raises(StageProcessingError, match="沒有可用的影像來源"):
        await stage._build_request(CardRow(ocr_source_page=1))


@pytest.mark.asyncio
async def test_both_paths_produce_the_same_card_shape(
    store: CardStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """共用 output_schema 的意義：兩條路徑產出的欄位集合必須完全相同。"""
    text_store = CardStore(tmp_path / "text" / "cards.csv")
    await text_store.write([CardRow(raw_text="□属する", ocr_source_page=1)])
    await ExtractStage(FakeLLMClient()).run(text_store)
    text_card = [r for r in await text_store.read() if r.card_id][0]

    vision_store = CardStore(tmp_path / "vision" / "cards.csv")
    await vision_store.write([_image_row(tmp_path)])
    await ExtractStage(FakeLLMClient(), settings=_vision_settings(monkeypatch)).run(vision_store)
    vision_card = [r for r in await vision_store.read() if r.card_id][0]

    assert text_card.model_dump().keys() == vision_card.model_dump().keys()
    assert text_card.front == vision_card.front


# ── 產出新列 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_produces_card_rows_and_keeps_source_row(store: CardStore) -> None:
    await store.write([CardRow(raw_text="□属する\n□続々", ocr_source_page=1)])
    client = FakeLLMClient(cards_per_call=2)

    result = await ExtractStage(client, source="單字書 p.333").run(store)

    assert (result.succeeded, result.added) == (1, 2)
    rows = await store.read()
    assert len(rows) == 3

    source_row = rows[0]
    assert source_row.card_id == ""
    assert source_row.raw_text.startswith("□属する")
    assert source_row.extract_status is StageStatus.DONE

    card = rows[1]
    assert card.card_id == "c1_1"
    assert card.front == "属する"
    assert card.reading == "ぞくする"
    assert card.tts_back_text == "虎はネコ科に属する。"
    assert card.difficulty == "3"
    assert card.ocr_source_page == 1
    assert card.source == "單字書 p.333"
    # 這張卡的抽取已完成，下游階段仍待處理
    assert card.extract_status is StageStatus.DONE
    assert card.image_status is StageStatus.PENDING
    assert card.audio_front_status is StageStatus.PENDING
    # 系統欄位一律留空
    assert card.created_at == ""
    assert card.image_front == ""


@pytest.mark.asyncio
async def test_deck_name_fills_in_when_model_leaves_it_empty(store: CardStore) -> None:
    await store.write([CardRow(raw_text="□属する")])
    client = FakeLLMClient()
    client.responses = [ExtractOutput(cards=[_card("a1", deck="")])]

    await ExtractStage(client, deck_name="日語::N2").run(store)

    assert (await store.read())[1].deck == "日語::N2"


@pytest.mark.asyncio
async def test_rerun_skips_everything(store: CardStore) -> None:
    """驗證續作：第二次執行不該再呼叫 LLM。"""
    await store.write([CardRow(raw_text="□属する")])
    client = FakeLLMClient()
    await ExtractStage(client).run(store)

    second = FakeLLMClient()
    result = await ExtractStage(second).run(store)

    assert second.calls == []
    assert result.processed == 0


@pytest.mark.asyncio
async def test_force_only_resends_source_rows(store: CardStore) -> None:
    """--force 會把已產出的卡片列也撈進來，它們必須被安靜略過而不是報錯。"""
    await store.write([CardRow(raw_text="□属する")])
    await ExtractStage(FakeLLMClient()).run(store)
    client = FakeLLMClient()
    client.responses = [_output(["brand_new"])]

    result = await ExtractStage(client).run(store, force=True)

    assert len(client.calls) == 1, "已抽取的卡片列不該再送給 LLM"
    assert result.failed == 0
    assert result.added == 1


@pytest.mark.asyncio
async def test_force_rerun_of_same_page_reports_duplicate(store: CardStore) -> None:
    """已知限制：重跑同一頁會撞上自己上次產出的 card_id。

    骨架目前只能新增列、不能移除列，因此無法自動汰換舊卡片。
    失敗訊息必須明確告知該怎麼做，而不是只丟一句 duplicated。
    """
    await store.write([CardRow(raw_text="□属する")])
    await ExtractStage(FakeLLMClient()).run(store)

    result = await ExtractStage(FakeLLMClient()).run(store, force=True)

    assert result.failed == 1
    source_row = (await store.read())[0]
    assert "card_id duplicated" in source_row.extract_error
    assert "移除該頁先前產出的卡片列" in source_row.extract_error


# ── card_id 唯一性 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_within_one_response_fails_the_row(store: CardStore) -> None:
    await store.write([CardRow(raw_text="□属する")])
    client = FakeLLMClient()
    client.responses = [_output(["a1", "a1"])]

    result = await ExtractStage(client).run(store)

    assert result.failed == 1
    rows = await store.read()
    assert len(rows) == 1, "重複時不應寫入任何新列"
    assert "card_id duplicated: a1" in rows[0].extract_error


@pytest.mark.asyncio
async def test_duplicate_across_rows_fails_the_second_row(store: CardStore) -> None:
    await store.write([CardRow(raw_text="頁一"), CardRow(raw_text="頁二")])
    client = FakeLLMClient()
    client.responses = [_output(["a1"]), _output(["a1"])]
    stage = ExtractStage(client)
    stage.concurrency = 1

    result = await stage.run(store)

    assert (result.succeeded, result.failed, result.added) == (1, 1, 1)
    rows = await store.read()
    assert rows[0].extract_status is StageStatus.DONE
    assert rows[1].extract_status is StageStatus.FAILED


@pytest.mark.asyncio
async def test_duplicate_of_existing_card_id_fails(store: CardStore) -> None:
    """唯一性要跨整份工作檔成立，不只是這一次執行。"""
    await store.write([CardRow(card_id="a1", extract_status=StageStatus.DONE),
                       CardRow(raw_text="□属する")])
    client = FakeLLMClient()
    client.responses = [_output(["a1"])]

    result = await ExtractStage(client).run(store)

    assert result.failed == 1
    assert "a1" in (await store.read())[1].extract_error


@pytest.mark.asyncio
async def test_blank_card_id_fails_the_row(store: CardStore) -> None:
    await store.write([CardRow(raw_text="□属する")])
    client = FakeLLMClient()
    client.responses = [ExtractOutput(cards=[_card("   ")])]

    result = await ExtractStage(client).run(store)

    assert result.failed == 1
    assert "沒有 card_id" in (await store.read())[0].extract_error


# ── 失敗處理與併發 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_failure_only_affects_that_row(store: CardStore) -> None:
    await store.write([CardRow(raw_text=f"頁{i}") for i in range(3)])
    client = FakeLLMClient()
    client.responses = [_output(["a1"]), None, _output(["a3"])]  # type: ignore[list-item]

    async def run_agent(agent_name: str, input_: AgentInput):
        client.calls.append((agent_name, input_))
        response = client.responses[len(client.calls) - 1]
        if response is None:
            raise TimeoutError("read timeout")
        return response

    client.run_agent = run_agent  # type: ignore[method-assign]
    stage = ExtractStage(client)
    stage.concurrency = 1

    result = await stage.run(store)

    assert (result.succeeded, result.failed, result.added) == (2, 1, 2)
    rows = await store.read()
    assert "TimeoutError" in rows[1].extract_error


@pytest.mark.asyncio
async def test_row_without_raw_text_or_card_id_fails(store: CardStore) -> None:
    await store.write([CardRow()])

    result = await ExtractStage(FakeLLMClient()).run(store)

    assert result.failed == 1
    assert "沒有 raw_text" in (await store.read())[0].extract_error


@pytest.mark.asyncio
async def test_non_extract_output_is_rejected(store: CardStore) -> None:
    class Other(BaseModel):
        text: str = ""

    await store.write([CardRow(raw_text="□属する")])
    client = FakeLLMClient()
    client.responses = [Other()]

    result = await ExtractStage(client).run(store)

    assert result.failed == 1
    assert "預期 ExtractOutput" in (await store.read())[0].extract_error


@pytest.mark.asyncio
async def test_concurrency_is_actually_applied(store: CardStore, settings) -> None:
    await store.write([CardRow(raw_text=f"頁{i}") for i in range(10)])
    client = FakeLLMClient()

    await ExtractStage(client, settings=settings).run(store)

    assert client.max_concurrent <= 3


# ── 需人工執行：真正呼叫 LLM ─────────────────────────────────────


@pytest.mark.manual
@pytest.mark.asyncio
async def test_real_extract_against_golden_page(tmp_path: Path) -> None:
    """以真實 ExtractAgent 抽取 fixtures 的第一頁，人工比對輸出品質。

    需要 Ollama 已載入 gemma4_31b_q4_K_M-optimized。判準見 tests/fixtures/README.md。
    """
    from anki_deck_builder.clients.llm_client import LLMClient

    page = Path("tests/fixtures/ocr_raw/page_01.txt").read_text(encoding="utf-8")  # noqa: ASYNC240
    store = CardStore(tmp_path / "cards.csv")
    await store.write([CardRow(raw_text=page, ocr_source_page=1)])

    stage = ExtractStage(
        LLMClient("agents.yaml"),
        deck_name="日語::N2",
        domain="日語 N2 單字",
        source="單字書 p.333",
        card_id_prefix="ja_n2",
    )
    result = await stage.run(store)

    assert result.failed == 0
    cards = [row for row in await store.read() if row.card_id]
    assert len(cards) >= 10, "第一頁有 13 個詞條，不該漏掉大半"
    for card in cards:
        assert card.card_id.startswith("ja_n2_p1_")
        assert card.deck.startswith("日語::N2::")
        assert card.front and card.back and card.reading
        assert card.image_prompt.endswith("no text, no letters, no watermark")
        assert card.tts_back_text and "\\n" not in card.tts_back_text
        assert card.created_at == ""
