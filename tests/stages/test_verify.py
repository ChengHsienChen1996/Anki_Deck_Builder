"""核對階段單元測試（Task 9.6）。

判定邏輯（白名單、範圍、標點、衝突）全部是純函式，**可完整執行**；
LLM 與版面偵測器一律以假實作替換，不載入任何模型、不讀任何影像。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from anki_deck_builder.config import OCRChunkSettings, Settings
from anki_deck_builder.schemas import CardRow
from anki_deck_builder.schemas.verify_output import Correction, VerifyOutput
from anki_deck_builder.stages.verify import (
    APPLIED,
    Proposal,
    _differs_beyond_punctuation,
    _judge,
    _propagate_reading,
    _resolve_conflicts,
    verify,
)
from anki_deck_builder.state import CardStore


def card(card_id: str = "p1_001", **kwargs) -> CardRow:
    values = {
        "card_id": card_id,
        "front": "緯度",
        "reading": "いど",
        "example": "緯度が高い。\n緯度很高。",
        "ocr_source_page": 1,
    }
    values.update(kwargs)
    return CardRow(**values)  # type: ignore[arg-type]


#: 假偵測器回報的頁面尺寸。實際影像必須一樣大，否則 `crop_to_b64` 會判定
#: 「偵測器把頁面轉正過」而跟著旋轉
PAGE_SIZE = (500, 1000)


@pytest.fixture
def page_image(tmp_path: Path) -> Path:
    """真的能被 Pillow 開啟的小圖——`crop_to_b64` 會實際裁切它。"""
    from PIL import Image

    path = tmp_path / "p.jpg"
    Image.new("RGB", PAGE_SIZE, (255, 255, 255)).save(path)
    return path


def source(page: int = 1, path: Path | str = "") -> CardRow:
    return CardRow(card_id="", source=str(path), ocr_source_page=page)


def proposal(card_id: str, after: str, verdict: str = APPLIED, chunk: int = 1) -> Proposal:
    return Proposal(
        page=1, chunk=chunk, scope="reading", card_id=card_id, field="reading",
        before="いど", after=after, reason="", verdict=verdict,
    )


# ── 判定：白名單與範圍 ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("reading", "いと", APPLIED),
        # 白名單以外一律不套用，但**保留提案**供人工挑
        ("front", "緯度々", "field not correctable"),
        ("back", "別的釋義", "field not correctable"),
        ("hint", "[名]", "field not correctable"),
        ("image_scene", "隨便", "field not correctable"),
    ],
)
def test_only_whitelisted_fields_are_applied(field, value, expected) -> None:
    verdict, _, _ = _judge(
        Correction(card_id="p1_001", field=field, value=value),
        scope="reading",
        cards={"p1_001": card()},
    )
    assert verdict == expected


def test_answering_outside_the_asked_scope_is_rejected() -> None:
    """本次只問 reading 卻回答 example，表示它沒有專注在被問的事情上。"""
    verdict, _, _ = _judge(
        Correction(card_id="p1_001", field="example", value="別的例句"),
        scope="reading",
        cards={"p1_001": card()},
    )
    assert verdict == "outside scope reading"


def test_unknown_card_id_is_rejected() -> None:
    verdict, before, _ = _judge(
        Correction(card_id="p9_999", field="reading", value="いと"),
        scope="reading",
        cards={"p1_001": card()},
    )
    assert verdict == "unknown card_id"
    assert before == ""


@pytest.mark.parametrize("value", ["いど", "  いど  ", ""])
def test_non_changes_are_rejected(value: str) -> None:
    """prompt 明寫「不要回報與現值相同的更正」，但仍會發生。"""
    verdict, _, _ = _judge(
        Correction(card_id="p1_001", field="reading", value=value),
        scope="reading",
        cards={"p1_001": card()},
    )
    assert verdict in {"no change", "empty value"}


# ── 判定：example 的兩道過濾 ─────────────────────────────────────


def test_example_separator_is_normalised_before_comparing() -> None:
    """模型回傳書上排版的 `原文／譯文`，套用專案自己的正規化後等於現值。

    Task 9.0 一度誤判「example 的更正多數把格式弄壞」，實際上是漏了這一步。
    """
    verdict, _, after = _judge(
        Correction(card_id="p1_001", field="example", value="緯度が高い。／緯度很高。"),
        scope="example",
        cards={"p1_001": card()},
    )
    assert after == "緯度が高い。\n緯度很高。"
    assert verdict == "no change"


def test_example_differing_only_in_punctuation_is_rejected() -> None:
    """9.0 第四輪：example 的變動多數只差句末的「。」。"""
    verdict, _, _ = _judge(
        Correction(card_id="p1_001", field="example", value="緯度が高い\n緯度很高"),
        scope="example",
        cards={"p1_001": card()},
    )
    assert verdict == "punctuation only"


def test_example_with_real_difference_is_applied() -> None:
    verdict, _, _ = _judge(
        Correction(card_id="p1_001", field="example", value="緯度が低い。\n緯度很低。"),
        scope="example",
        cards={"p1_001": card()},
    )
    assert verdict == APPLIED


@pytest.mark.parametrize(
    ("before", "after", "differs"),
    [
        ("あ。", "あ", False),
        ("あ、い", "あい", False),
        ("あい", "あう", True),
        # 小寫假名不是標點——`医療` 的 `いりよう`／`いりょう` 正是這一類
        ("いりよう", "いりょう", True),
    ],
)
def test_differs_beyond_punctuation(before, after, differs) -> None:
    assert _differs_beyond_punctuation(before, after) is differs


# ── 塊間衝突 ─────────────────────────────────────────────────────


def test_chunks_disagreeing_rejects_both() -> None:
    """9.0 第三輪：塊間不一致是免費的信心指標，兩塊給不同答案的正好都可疑。"""
    proposals = [proposal("p1_001", "いと", chunk=1), proposal("p1_001", "いど う", chunk=2)]

    _resolve_conflicts(proposals)

    assert [p.verdict for p in proposals] == ["conflicting chunks"] * 2


def test_chunks_agreeing_applies_once() -> None:
    proposals = [proposal("p1_001", "いと", chunk=1), proposal("p1_001", "いと", chunk=2)]

    _resolve_conflicts(proposals)

    assert [p.verdict for p in proposals] == [APPLIED, "duplicate"]


def test_conflicts_are_scoped_to_one_card_and_field() -> None:
    proposals = [proposal("p1_001", "いと"), proposal("p1_002", "べつ")]

    _resolve_conflicts(proposals)

    assert all(p.applied for p in proposals)


def test_already_rejected_proposals_do_not_create_conflicts() -> None:
    """被拒絕的提案不參與衝突判定，否則一筆雜訊會連坐掉一筆好的更正。"""
    proposals = [
        proposal("p1_001", "いと"),
        proposal("p1_001", "亂answer", verdict="field not correctable"),
    ]

    _resolve_conflicts(proposals)

    assert proposals[0].verdict == APPLIED


# ── 端到端（假 client、假偵測器）──────────────────────────────────


class FakeDetector:
    """回傳兩個文字框，讓 plan() 切出可用的塊。"""

    def __init__(self, boxes=None, error: Exception | None = None) -> None:
        self.error = error
        self._boxes = boxes if boxes is not None else [
            {"box": (0, 0, 500, 400), "cls": "plain text"},
            {"box": (0, 600, 500, 990), "cls": "plain text"},
        ]

    def detect(self, image_path):
        if self.error is not None:
            raise self.error
        return self._boxes, PAGE_SIZE


class FakeVerifyClient:
    """記錄每次呼叫的 prompt，依 scope 回傳預設好的更正。"""

    def __init__(self, by_scope: dict[str, list[Correction]] | None = None) -> None:
        self.by_scope = by_scope or {}
        self.prompts: list[str] = []
        self.agents: list[str] = []

    async def run_agent(self, agent_name: str, input_):
        self.agents.append(agent_name)
        # `build_image_input` 把文字放在第二則 message 的 content（純字串）
        prompt = next(
            message["content"]
            for message in input_
            if isinstance(message["content"], str)
        )
        self.prompts.append(prompt)
        scope = "reading" if "`reading`" in prompt.split("\n")[0] else "example"
        return VerifyOutput(corrections=list(self.by_scope.get(scope, [])))


def settings_for(tmp_path: Path) -> Settings:
    return Settings.model_construct(
        ocr_chunk=OCRChunkSettings.model_construct(enabled=True, budget_px=400_000)
    )


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "work" / "cards.csv")


async def run_verify(store: CardStore, rows, client, **kwargs):
    await store.write(rows)
    report = await verify(
        store,
        client,
        FakeDetector(),
        settings_for(store.path.parent),
        notify=lambda _: None,
        **kwargs,
    )
    return report, await store.read()


@pytest.mark.asyncio
async def test_applies_a_reading_correction(store: CardStore, page_image: Path) -> None:
    client = FakeVerifyClient(
        {"reading": [Correction(card_id="p1_001", field="reading", value="いりょう")]}
    )

    rows_in = [source(1, page_image), card()]

    report, rows = await run_verify(store, rows_in, client, scopes=("reading",))

    assert report.applied == 1
    assert [r.reading for r in rows if r.card_id] == ["いりょう"]


@pytest.mark.asyncio
async def test_dry_run_leaves_the_work_file_untouched(store: CardStore, page_image: Path) -> None:
    client = FakeVerifyClient(
        {"reading": [Correction(card_id="p1_001", field="reading", value="いりょう")]}
    )

    report, rows = await run_verify(
        store, [source(1, page_image), card()], client, scopes=("reading",), dry_run=True
    )

    assert report.applied == 1
    assert [r.reading for r in rows if r.card_id] == ["いど"]


@pytest.mark.asyncio
async def test_one_call_per_chunk_per_scope(store: CardStore, page_image: Path) -> None:
    """一次只送一個範圍——合併會讓專注度下降（9.0 第四輪）。"""
    client = FakeVerifyClient()

    report, _ = await run_verify(store, [source(1, page_image), card()], client)

    assert report.calls == len(client.prompts)
    assert {p.split("\n")[0] for p in client.prompts} == {
        "本次只核對 `reading` 這一個欄位，其他欄位一律不要回報。",
        "本次只核對 `example` 這一個欄位，其他欄位一律不要回報。",
    }


@pytest.mark.asyncio
async def test_the_card_list_carries_front_as_given(store: CardStore, page_image: Path) -> None:
    """prompt 要模型把 front 當既定事實，不帶的話它會回頭自己判斷詞條。"""
    client = FakeVerifyClient()

    await run_verify(store, [source(1, page_image), card()], client, scopes=("reading",))

    assert "p1_001\t緯度\tいど" in client.prompts[0]


@pytest.mark.asyncio
async def test_a_failing_call_does_not_stop_the_batch(store: CardStore, page_image: Path) -> None:
    class Boom(FakeVerifyClient):
        async def run_agent(self, agent_name, input_):
            self.agents.append(agent_name)
            if len(self.agents) == 1:
                raise RuntimeError("模型掛了")
            return VerifyOutput(corrections=[])

    client = Boom()

    rows_in = [source(1, page_image), card()]

    report, _ = await run_verify(store, rows_in, client, scopes=("reading",))

    assert report.failed_calls == 1
    assert report.calls > 1


@pytest.mark.asyncio
async def test_detection_failure_skips_the_page(store: CardStore, page_image: Path) -> None:
    await store.write([source(1, page_image), card()])

    report = await verify(
        store,
        FakeVerifyClient(),
        FakeDetector(error=RuntimeError("偵測器掛了")),
        settings_for(store.path.parent),
        notify=lambda _: None,
    )

    assert report.pages == 0 and report.calls == 0


@pytest.mark.asyncio
async def test_pages_filter(store: CardStore, page_image: Path) -> None:
    client = FakeVerifyClient()

    report, _ = await run_verify(
        store,
        [
            source(1, page_image), card("p1_001"),
            source(2, page_image), card("p2_001", ocr_source_page=2),
        ],
        client,
        pages=[2],
        scopes=("reading",),
    )

    assert report.pages == 1
    assert "p2_001" in client.prompts[0]


@pytest.mark.asyncio
async def test_corrections_file_records_rejected_proposals(
    store: CardStore, page_image: Path
) -> None:
    """稽核檔是 front／hint／back 那類「不自動套用但值得人工挑」的唯一出口。"""
    client = FakeVerifyClient(
        {
            "reading": [
                Correction(
                    card_id="p1_001", field="reading", value="いりょう", reason="影像上是拗音"
                ),
                Correction(card_id="p1_001", field="front", value="医療", reason="形近字"),
            ]
        }
    )

    rows_in = [source(1, page_image), card()]

    report, _ = await run_verify(store, rows_in, client, scopes=("reading",))

    rows = list(csv.DictReader(report.corrections_path.open(encoding="utf-8")))
    assert {r["field"] for r in rows} == {"reading", "front"}
    rejected = next(r for r in rows if r["field"] == "front")
    assert rejected["verdict"] == "field not correctable"
    assert rejected["after"] == "医療" and rejected["reason"] == "形近字"


@pytest.mark.asyncio
async def test_no_proposals_writes_no_file(store: CardStore, page_image: Path) -> None:
    report, _ = await run_verify(store, [source(1, page_image), card()], FakeVerifyClient())

    assert report.corrections_path is None


# ── example 的第三道過濾：原文行混進譯文（2026-09-04 實測補的）─────


@pytest.mark.parametrize(
    "value",
    [
        "衣服を整える／整裝。\n整理衣物。",
        "豆を煎る / 炒豆子。\n炒豆子。",
        "医療機関／医療機構\n醫療機構",
    ],
)
def test_translation_leaking_into_the_source_line_is_rejected(value: str) -> None:
    """第 11 頁實測：example 的 7 筆自動更正有 5 筆是這個形態。

    第一行正是 `tts_back_text` 的來源，套用等於讓語音把譯文一起唸出來。
    """
    verdict, _, _ = _judge(
        Correction(card_id="p1_001", field="example", value=value),
        scope="example",
        cards={"p1_001": card()},
    )
    assert verdict == "translation in source line"


def test_a_separator_in_the_translation_line_is_fine() -> None:
    """只看第一行——譯文那一行本來就可能含斜線。"""
    verdict, _, _ = _judge(
        Correction(card_id="p1_001", field="example", value="緯度が低い。\n緯度低／偏低。"),
        scope="example",
        cards={"p1_001": card()},
    )
    assert verdict == APPLIED


# ── reading 的兩道補強（2026-09-04 全批實測補的）─────────────────


@pytest.mark.parametrize("value", ["あ（つ）", "あれ(っ)", "え(つ)", "い〔ち〕", "お[か]ず"])
def test_notation_in_reading_is_rejected(value: str) -> None:
    """書上的括號標註會被語音唸出來。全批 38 筆 reading 更正裡有 3 筆是這形態。

    拒絕而不是清掉：`あ（つ）` 去掉括號是 `あつ` 還是 `あ`，程式猜不出來。
    """
    verdict, _, _ = _judge(
        Correction(card_id="p1_001", field="reading", value=value),
        scope="reading",
        cards={"p1_001": card()},
    )
    assert verdict == "notation in reading"


def test_slash_stays_allowed_in_reading() -> None:
    """斜線是本專案認可的多讀音寫法（stages/audio.py 會取第一個）。"""
    verdict, _, _ = _judge(
        Correction(card_id="p1_001", field="reading", value="いど/いと"),
        scope="reading",
        cards={"p1_001": card()},
    )
    assert verdict == APPLIED


def test_reading_correction_propagates_to_the_tts_text() -> None:
    """否則核對修好顯示用的讀音，語音仍照著錯的唸——修了一半更難發現。"""
    row = card(reading="いりよう", tts_front_text="いりよう")

    _propagate_reading(row, before="いりよう", after="いりょう")

    assert row.tts_front_text == "いりょう"


@pytest.mark.parametrize(
    ("tts_before", "expected"),
    [
        # 等於 front 本身：沒有讀音概念的領域，不是從讀音導出的
        ("緯度", "緯度"),
        # 人工編修過的值
        ("いど（ゆっくり）", "いど（ゆっくり）"),
        ("", ""),
    ],
)
def test_propagation_only_touches_values_derived_from_the_reading(
    tts_before: str, expected: str
) -> None:
    row = card(reading="いりよう", tts_front_text=tts_before)

    _propagate_reading(row, before="いりよう", after="いりょう")

    assert row.tts_front_text == expected


@pytest.mark.asyncio
async def test_applying_a_reading_updates_the_tts_text_end_to_end(
    store: CardStore, page_image: Path
) -> None:
    client = FakeVerifyClient(
        {"reading": [Correction(card_id="p1_001", field="reading", value="いりょう")]}
    )
    rows_in = [source(1, page_image), card(reading="いりよう", tts_front_text="いりよう")]

    _, rows = await run_verify(store, rows_in, client, scopes=("reading",))

    changed = next(r for r in rows if r.card_id)
    assert (changed.reading, changed.tts_front_text) == ("いりょう", "いりょう")
