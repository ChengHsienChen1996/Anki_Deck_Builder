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
from anki_deck_builder.stages.extract import (
    DETECT_AGENT,
    ENRICH_AGENT,
    TEXT_AGENT,
    VISION_AGENT,
    ExtractStage,
    estimate_entries,
    split_into_chunks,
)
from anki_deck_builder.state import CardStore


class FakeLLMClient:
    """依 Protocol 注入的假 client，攔在任何真實呼叫之前。"""

    #: `MaterialTypeAgent` 的回覆。預設「有釋義」，讓多數測試不必理會補釋義路徑
    material_verdict = "HAS_DEFINITIONS"

    def __init__(
        self,
        *,
        cards_per_call: int = 1,
        error: Exception | None = None,
        fail_over_lines: int | None = None,
    ) -> None:
        self.cards_per_call = cards_per_call
        self.error = error
        #: 超過這個行數就丟錯，模擬本地模型面對太多條目時的退化
        self.fail_over_lines = fail_over_lines
        self.calls: list[tuple[str, AgentInput]] = []
        #: 教材判斷的呼叫另外記，避免污染「抽取呼叫了幾次」的斷言
        self.detect_calls: list[AgentInput] = []
        self.responses: list[BaseModel] | None = None
        self.max_concurrent = 0
        self._active = 0

    async def run_agent(self, agent_name: str, input_: AgentInput) -> BaseModel | str:
        if agent_name == DETECT_AGENT:
            self.detect_calls.append(input_)
            return self.material_verdict
        self._active += 1
        self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            self.calls.append((agent_name, input_))
            if self.error is not None:
                raise self.error
            if (
                self.fail_over_lines is not None
                and isinstance(input_, str)
                and len(input_.splitlines()) > self.fail_over_lines
            ):
                raise RuntimeError("Invalid JSON when parsing model output")
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


def test_concurrency_is_fixed_at_one(settings) -> None:
    """本地模型序列化執行，併發只會讓後面的請求在排隊時燒掉逾時預算：
    單頁約 157s，四列並送時最後一列光排隊就超過 timeout: 600（實測）。"""
    assert ExtractStage(FakeLLMClient(), settings=settings).concurrency == 1


def test_concurrency_ignores_global_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GLOBAL_CONCURRENCY 調高也不該讓本階段並送。"""
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("YAML_SETTINGS_FILE", "agents.yaml")
    monkeypatch.setenv("GLOBAL_CONCURRENCY", "6")
    settings = load_settings(env_file=None)

    assert ExtractStage(FakeLLMClient(), settings=settings).concurrency == 1


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
    # 有前綴可用時（此處由頁碼推得 p1），card_id 由本階段重新編號，
    # 不沿用模型自報的值——見 _renumber
    assert card.card_id == "p1_001"
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
        if agent_name == DETECT_AGENT:
            return client.material_verdict
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


# ── 切段（長頁面）────────────────────────────────────────────────


def test_split_keeps_short_text_in_one_chunk() -> None:
    """放得下就別切——小頁面的行為與未切段時完全相同。"""
    text = "□属する\nぞくする\n[自サ] 屬於"

    assert split_into_chunks(text, 20) == [text]


def test_split_breaks_long_text() -> None:
    text = "\n".join(f"word{i} (n)" for i in range(50))

    chunks = split_into_chunks(text, 20)

    assert len(chunks) > 1
    assert sum(len([line for line in c.splitlines() if line.strip()]) for c in chunks) == 50


def test_split_prefers_blank_lines_as_boundaries() -> None:
    """空行通常是條目之間的分界，從那裡切開才不會把一個條目切成兩半。"""
    entries = [f"□詞{i}\nよみ{i}\n[名] 釋義{i}" for i in range(12)]
    text = "\n\n".join(entries)

    chunks = split_into_chunks(text, 6)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.strip().startswith("□")


def test_split_ignores_empty_text() -> None:
    assert split_into_chunks("   \n\n", 20) == []


@pytest.mark.asyncio
async def test_long_page_is_sent_in_several_calls(store: CardStore) -> None:
    client = FakeLLMClient()
    await store.write([CardRow(raw_text="\n".join(f"word{i} (n)" for i in range(60)))])

    result = await ExtractStage(client, card_id_prefix="en").run(store)

    assert len(client.calls) > 1
    assert result.added == len(client.calls)


@pytest.mark.asyncio
async def test_each_chunk_gets_its_own_card_id_prefix(store: CardStore) -> None:
    """每段是獨立一次呼叫、序號都從 001 起算，沒有段落標籤就會整批撞號。"""
    client = FakeLLMClient()
    await store.write(
        [CardRow(raw_text="\n".join(f"word{i} (n)" for i in range(60)), ocr_source_page=1)]
    )

    await ExtractStage(client, card_id_prefix="en").run(store)

    prefixes = [
        line
        for _, sent in client.calls
        for line in str(sent).splitlines()
        if "卡片ID前綴" in line
    ]
    assert len(prefixes) == len(set(prefixes)) > 1
    assert all("_p1_b" in p for p in prefixes)


@pytest.mark.asyncio
async def test_failed_chunk_is_halved_and_retried(store: CardStore) -> None:
    """本地模型面對太多條目不會報錯，而是退化。對半切讓它自己收斂到可行粒度。"""
    client = FakeLLMClient(fail_over_lines=16)
    await store.write([CardRow(raw_text="\n".join(f"word{i} (n)" for i in range(40)))])

    result = await ExtractStage(client).run(store)

    assert result.failed == 0
    assert result.added > 0


@pytest.mark.asyncio
async def test_gives_up_when_halving_does_not_help(store: CardStore) -> None:
    """切到下限仍失敗就是別的問題，繼續切只是浪費呼叫。"""
    client = FakeLLMClient()
    client.error = RuntimeError("model is broken")
    await store.write([CardRow(raw_text="\n".join(f"word{i} (n)" for i in range(40)))])

    result = await ExtractStage(client).run(store)

    assert result.failed == 1
    assert "抽取呼叫失敗" in (await store.read())[0].extract_error


@pytest.mark.asyncio
async def test_card_ids_are_renumbered_across_chunks(store: CardStore) -> None:
    """切段後每段的序號都從 001 起算，若沿用模型自報的 id 就會整批撞號。"""
    client = FakeLLMClient(cards_per_call=2)
    await store.write(
        [CardRow(raw_text="\n".join(f"word{i} (n)" for i in range(60)), ocr_source_page=3)]
    )

    await ExtractStage(client, card_id_prefix="en").run(store)

    ids = [r.card_id for r in await store.read() if r.card_id]
    assert ids == [f"en_p3_{i:03d}" for i in range(1, len(ids) + 1)]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_model_ids_are_kept_when_no_prefix_available(store: CardStore) -> None:
    """沒有前綴也沒有頁碼時維持 Phase 1 行為：由模型依領域與讀音自行取名。"""
    client = FakeLLMClient(cards_per_call=2)
    await store.write([CardRow(raw_text="□属する")])

    await ExtractStage(client).run(store)

    assert [r.card_id for r in await store.read() if r.card_id] == ["c1_1", "c1_2"]


# ── 釋義語言 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_card_language_is_sent_as_task_parameter() -> None:
    """原文書與純單字表沒有既有譯文，模型會跟著教材語言走；此參數把它拉回來。"""
    stage = ExtractStage(FakeLLMClient(), card_language="繁體中文")

    _, input_ = await stage._build_request(CardRow(raw_text="ability (n)"))

    assert "釋義語言: 繁體中文" in input_


@pytest.mark.asyncio
async def test_card_language_is_omitted_when_unset() -> None:
    stage = ExtractStage(FakeLLMClient(), deck_name="英語::基礎")

    _, input_ = await stage._build_request(CardRow(raw_text="ability (n)"))

    assert "釋義語言" not in input_


@pytest.mark.asyncio
async def test_card_language_reaches_vision_path_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """兩條輸入路徑共用任務參數，設定不能只在其中一條生效。"""
    stage = ExtractStage(
        FakeLLMClient(), settings=_vision_settings(monkeypatch), card_language="繁體中文"
    )

    _, input_ = await stage._build_request(_image_row(tmp_path))

    assert "釋義語言: 繁體中文" in input_[1]["content"]


# ── 產出不足的偵測 ───────────────────────────────────────────────


def test_estimate_entries_counts_strong_markers() -> None:
    text = "□増大\nぞうだい\n[名] 增多\n\n□装置\nそうち\n[名] 裝置"

    assert estimate_entries(text) == 2


def test_estimate_entries_ignores_bullets() -> None:
    """`•` 在多數教材裡是例句或子項，算進去會高估條目數、觸發不必要的重試。"""
    text = "□able\n• be able to\n• not able to\n\n□about\n• about 500 students"

    assert estimate_entries(text) == 2


def test_estimate_entries_counts_numbered_lines() -> None:
    assert estimate_entries("1. first\n2. second\n3. third") == 3


def test_estimate_entries_returns_zero_without_markers() -> None:
    """一行一詞的索引式單字表估不出條目數——回 0，呼叫端據此跳過檢查。"""
    assert estimate_entries("ability (n)\nable (adj)\nabout (adv)") == 0


@pytest.mark.asyncio
async def test_silent_under_production_triggers_retry(store: CardStore) -> None:
    """模型偶爾呼叫成功、JSON 合法，卻只回一張卡就收工（實測 7 個條目回 1 張）。
    沒有這道檢查，缺掉的卡片會直接寫進工作檔，使用者無從察覺。"""
    entries = "\n\n".join(f"□詞{i}\nよみ{i}\n[名] 釋義{i}" for i in range(8))
    await store.write([CardRow(raw_text=entries)])

    client = FakeLLMClient(cards_per_call=1)
    result = await ExtractStage(client).run(store)

    # 一張卡對八個條目 → 判定產出不足 → 對半重試直到每段的條目數夠少
    assert len(client.calls) > 1
    assert result.added > 1


@pytest.mark.asyncio
async def test_full_yield_does_not_trigger_retry(store: CardStore) -> None:
    entries = "\n\n".join(f"□詞{i}\nよみ{i}\n[名] 釋義{i}" for i in range(4))
    await store.write([CardRow(raw_text=entries)])

    client = FakeLLMClient(cards_per_call=4)
    await ExtractStage(client).run(store)

    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_no_check_when_entries_cannot_be_estimated(store: CardStore) -> None:
    """索引式單字表沒有條目符號，寧可不檢查也不要用不可靠的估計觸發重試。"""
    await store.write([CardRow(raw_text="\n".join(f"word{i} (n)" for i in range(8)))])

    client = FakeLLMClient(cards_per_call=1)
    await ExtractStage(client).run(store)

    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_deck_categories_is_sent_as_task_parameter() -> None:
    """教材屬於哪個領域只有呼叫端知道，寫死進 prompt 會把工具綁死在語言類教材上。"""
    stage = ExtractStage(FakeLLMClient(), deck_categories="心血管藥物／抗感染藥／其他")

    _, input_ = await stage._build_request(CardRow(raw_text="□β受體阻斷劑\n[藥理分類] 阻斷…"))

    assert "分類選項: 心血管藥物／抗感染藥／其他" in input_


@pytest.mark.asyncio
async def test_deck_categories_is_omitted_when_unset() -> None:
    """不傳就沿用 prompt 的領域範例自行判斷，不該變成必填。"""
    stage = ExtractStage(FakeLLMClient(), card_language="繁體中文")

    _, input_ = await stage._build_request(CardRow(raw_text="□詞\nよみ"))

    assert "分類選項" not in input_


# ── 補釋義（索引式教材）─────────────────────────────────────────


class EnrichingFakeClient(FakeLLMClient):
    """補釋義回純文字、抽取回卡片，模擬兩個 agent 的不同輸出型態。"""

    async def run_agent(self, agent_name: str, input_: AgentInput) -> object:
        if agent_name == ENRICH_AGENT:
            self.calls.append((agent_name, input_))
            body = str(input_).split("\n\n", 1)[-1]
            return "\n\n".join(
                f"□{line.strip()}\n[名詞] 補上的釋義\n例句／譯文"
                for line in body.splitlines()
                if line.strip()
            )
        return await super().run_agent(agent_name, input_)


@pytest.mark.asyncio
async def test_enrich_runs_before_extraction(store: CardStore) -> None:
    client = EnrichingFakeClient()
    await store.write([CardRow(raw_text="ability (n)\nable (adj)")])

    await ExtractStage(client, enrich=True).run(store)

    agents = [name for name, _ in client.calls]
    assert agents == [ENRICH_AGENT, TEXT_AGENT]


@pytest.mark.asyncio
async def test_extraction_receives_the_enriched_text(store: CardStore) -> None:
    client = EnrichingFakeClient()
    await store.write([CardRow(raw_text="ability (n)")])

    await ExtractStage(client, enrich=True).run(store)

    _, sent = client.calls[-1]
    assert "補上的釋義" in str(sent)


@pytest.mark.asyncio
async def test_enrich_is_skipped_when_material_has_definitions(store: CardStore) -> None:
    """自動判斷為「有釋義」時不該多付一次補釋義呼叫。"""
    client = EnrichingFakeClient()
    client.material_verdict = "HAS_DEFINITIONS"
    await store.write([CardRow(raw_text="□属する\nぞくする\n[自サ] 屬於")])

    await ExtractStage(client).run(store)

    assert [name for name, _ in client.calls] == [TEXT_AGENT]
    assert len(client.detect_calls) == 1


@pytest.mark.asyncio
async def test_enrich_runs_when_material_lacks_definitions(store: CardStore) -> None:
    """索引式教材由模型自行認出，使用者不必記得加旗標。"""
    client = EnrichingFakeClient()
    client.material_verdict = "NO_DEFINITIONS"
    await store.write([CardRow(raw_text="ability (n)\nable (adj)")])

    await ExtractStage(client).run(store)

    assert [name for name, _ in client.calls] == [ENRICH_AGENT, TEXT_AGENT]


@pytest.mark.asyncio
async def test_explicit_flag_overrides_detection(store: CardStore) -> None:
    """自動判斷失準時要有辦法強制。"""
    client = EnrichingFakeClient()
    client.material_verdict = "NO_DEFINITIONS"
    await store.write([CardRow(raw_text="□属する\nぞくする\n[自サ] 屬於")])

    await ExtractStage(client, enrich=False).run(store)

    assert [name for name, _ in client.calls] == [TEXT_AGENT]
    assert client.detect_calls == []


@pytest.mark.asyncio
async def test_detection_is_asked_once_per_row(store: CardStore) -> None:
    """教材性質整頁一致，逐段問只是重複付費。"""
    client = EnrichingFakeClient()
    await store.write([CardRow(raw_text="\n".join(f"word{i} (n)" for i in range(60)))])

    await ExtractStage(client).run(store)

    assert len(client.detect_calls) == 1


@pytest.mark.asyncio
async def test_detection_failure_falls_back_to_no_enrichment(store: CardStore) -> None:
    """判斷失敗不該讓整列失敗——保守地不補，以免覆蓋掉原文既有的釋義。"""

    class BrokenDetect(EnrichingFakeClient):
        async def run_agent(self, agent_name: str, input_: AgentInput) -> object:
            if agent_name == DETECT_AGENT:
                raise RuntimeError("detect exploded")
            return await super().run_agent(agent_name, input_)

    client = BrokenDetect()
    await store.write([CardRow(raw_text="□属する\nぞくする\n[自サ] 屬於")])

    result = await ExtractStage(client).run(store)

    assert result.failed == 0
    assert [name for name, _ in client.calls] == [TEXT_AGENT]


@pytest.mark.asyncio
async def test_too_short_enrichment_is_treated_as_failure(store: CardStore) -> None:
    """補完的結果比原文還短，代表模型漏掉了條目。"""

    class LazyEnrich(FakeLLMClient):
        async def run_agent(self, agent_name: str, input_: AgentInput) -> object:
            if agent_name == ENRICH_AGENT:
                self.calls.append((agent_name, input_))
                return "□a\n[名詞] 短"
            return await super().run_agent(agent_name, input_)

    client = LazyEnrich()
    await store.write([CardRow(raw_text="\n".join(f"word{i} (n)" for i in range(12)))])

    result = await ExtractStage(client, enrich=True).run(store)

    assert result.failed == 1
    assert "補釋義" in (await store.read())[0].extract_error


@pytest.mark.asyncio
async def test_empty_required_fields_trigger_retry(store: CardStore) -> None:
    """數量正確、內容全空——實測看過整頁 59 張卡的 back 與 example 都是空的。
    這種卡一定會在 pack 被擋下，不如在產生它的階段就重試。"""

    class EmptyBackClient(FakeLLMClient):
        attempts = 0

        async def run_agent(self, agent_name: str, input_: AgentInput) -> object:
            if agent_name == DETECT_AGENT:
                return self.material_verdict
            EmptyBackClient.attempts += 1
            self.calls.append((agent_name, input_))
            # 第一次回空 back，重試後才正常
            if EmptyBackClient.attempts == 1:
                return ExtractOutput(cards=[_card("x1", back=""), _card("x2", back="")])
            return _output([f"ok{EmptyBackClient.attempts}"])

    EmptyBackClient.attempts = 0
    await store.write(
        [CardRow(raw_text="\n\n".join(f"□詞{i}\nよみ{i}\n[名] 釋義{i}" for i in range(8)))]
    )

    result = await ExtractStage(EmptyBackClient()).run(store)

    assert result.failed == 0
    assert all(r.back for r in await store.read() if r.card_id)


@pytest.mark.asyncio
async def test_persistently_empty_fields_fail_the_row(store: CardStore) -> None:
    """重試到底仍是空的，就讓該列失敗——訊息要指出是必填欄位為空，
    而不是等到 pack 才報。"""

    class AlwaysEmpty(FakeLLMClient):
        async def run_agent(self, agent_name: str, input_: AgentInput) -> object:
            if agent_name == DETECT_AGENT:
                return self.material_verdict
            self.calls.append((agent_name, input_))
            return ExtractOutput(cards=[_card("x1", back="")])

    await store.write([CardRow(raw_text="□詞\nよみ\n[名] 釋義")])

    result = await ExtractStage(AlwaysEmpty()).run(store)

    assert result.failed == 1
    assert "必填欄位為空" in (await store.read())[0].extract_error


@pytest.mark.asyncio
async def test_vision_path_retries_on_empty_fields(
    store: CardStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """影像路徑也會整批產出空 back（實測一頁 59 張全空）。它無法切段，
    但退化多半是隨機的，重試一次就能拿到正常結果。"""

    class FlakyVision(FakeLLMClient):
        attempts = 0

        async def run_agent(self, agent_name: str, input_: AgentInput) -> object:
            if agent_name == DETECT_AGENT:
                return self.material_verdict
            FlakyVision.attempts += 1
            self.calls.append((agent_name, input_))
            if FlakyVision.attempts == 1:
                return ExtractOutput(cards=[_card("v1", back="")])
            return _output(["v_ok"])

    FlakyVision.attempts = 0
    await store.write([_image_row(tmp_path)])

    result = await ExtractStage(FlakyVision(), settings=_vision_settings(monkeypatch)).run(store)

    assert result.failed == 0
    assert [r.back for r in await store.read() if r.card_id] == ["屬於，歸於"]


@pytest.mark.asyncio
async def test_vision_path_fails_after_retry(
    store: CardStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class AlwaysEmptyVision(FakeLLMClient):
        async def run_agent(self, agent_name: str, input_: AgentInput) -> object:
            if agent_name == DETECT_AGENT:
                return self.material_verdict
            self.calls.append((agent_name, input_))
            return ExtractOutput(cards=[_card("v1", back="")])

    client = AlwaysEmptyVision()
    await store.write([_image_row(tmp_path)])

    result = await ExtractStage(client, settings=_vision_settings(monkeypatch)).run(store)

    assert result.failed == 1
    assert len(client.calls) == 2  # 原樣重試一次就放棄
    assert "必填欄位為空" in (await store.read())[0].extract_error
