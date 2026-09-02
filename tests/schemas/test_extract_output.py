"""ExtractOutput / ExtractedCard 單元測試。"""

import pytest
from pydantic import ValidationError

from anki_deck_builder.schemas import CardRow, ExtractedCard, ExtractOutput
from anki_deck_builder.schemas.extract_output import derive_tts_back_text

MINIMAL = {"card_id": "a1", "deck": "日語::N2::動詞", "front": "属する", "back": "屬於"}


def test_minimal_card_uses_defaults() -> None:
    card = ExtractedCard(**MINIMAL)

    assert card.card_type == "basic"
    assert card.difficulty == 3
    assert card.reading == ""
    assert card.tts_front_text == ""


@pytest.mark.parametrize("missing", sorted(MINIMAL))
def test_required_fields_are_required(missing: str) -> None:
    data = {k: v for k, v in MINIMAL.items() if k != missing}

    with pytest.raises(ValidationError):
        ExtractedCard(**data)


def test_to_card_fields_are_all_card_row_fields() -> None:
    fields = ExtractedCard(**MINIMAL).to_card_fields()

    assert set(fields) <= set(CardRow.field_order())
    assert all(isinstance(v, str) for v in fields.values())


def test_to_card_fields_stringifies_difficulty() -> None:
    fields = ExtractedCard(**MINIMAL, difficulty=4).to_card_fields()

    assert fields["difficulty"] == "4"


def test_to_card_fields_can_populate_a_card_row() -> None:
    card = ExtractedCard(**MINIMAL, reading="ぞくする", tts_front_text="ぞくする")

    row = CardRow(**card.to_card_fields())

    assert row.card_id == "a1"
    assert row.reading == "ぞくする"
    # extract 不碰狀態欄位與系統欄位
    assert row.created_at == ""
    assert row.image_front == ""


def test_extract_does_not_produce_the_image_layers() -> None:
    """語義層與語法層自 Phase 8 起各自成階段——抽取不該再碰這兩欄。

    這是回歸保護：把 image_prompt 加回 ExtractedCard 就等於把創意視覺轉譯
    塞回那個已經過載的呼叫（見 stages/scene.py 的模組 docstring）。
    """
    assert "image_prompt" not in ExtractedCard.model_fields
    assert "image_scene" not in ExtractedCard.model_fields

    fields = ExtractedCard(**MINIMAL).to_card_fields()
    assert "image_prompt" not in fields
    assert "image_scene" not in fields


def test_output_holds_multiple_cards() -> None:
    """一段 raw_text（通常一整頁）可切出多張卡。"""
    output = ExtractOutput(
        cards=[
            ExtractedCard(**MINIMAL),
            ExtractedCard(card_id="a2", deck="日語::N2::名詞", front="装置", back="裝置"),
        ]
    )

    assert len(output.cards) == 2
    assert output.cards[1].card_id == "a2"


def test_output_defaults_to_empty_list() -> None:
    assert ExtractOutput().cards == []


def test_output_parses_from_plain_dict() -> None:
    """agent_factory 依 output_schema 解析回傳值，形狀須能由 dict 直接驗證。"""
    output = ExtractOutput.model_validate({"cards": [MINIMAL]})

    assert output.cards[0].front == "属する"


# ── 例句分隔符正規化 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A dog. / 一隻狗。", "A dog.\n一隻狗。"),
        ("A dog。／一隻狗。", "A dog。\n一隻狗。"),
        ("  A dog. / 一隻狗。  ", "A dog.\n一隻狗。"),
    ],
)
def test_example_separator_is_normalised(raw: str, expected: str) -> None:
    """實測 308 張卡有 176 張（57%）用斜線而非換行——prompt 講了，模型沒照做。

    分隔符轉換是確定性的，用規則做一次就穩；靠模型服從度做，每跑一批就重擲骰子。
    """
    assert ExtractedCard(**MINIMAL, example=raw).example == expected


@pytest.mark.parametrize(
    "raw",
    [
        "A dog.\n一隻狗。",           # 已經合規：一個字都不碰
        "A dog.",                     # 只有原文，不是錯誤
        "I like a/b testing.",        # 句中裸斜線，不可誤切（開發時實際踩到）
        "／只有譯文",                  # 分隔符在頭
        "原文／",                      # 分隔符在尾
        "",
    ],
)
def test_example_is_left_alone_when_there_is_nothing_to_split(raw: str) -> None:
    assert ExtractedCard(**MINIMAL, example=raw).example == raw


def test_normalisation_applies_to_the_card_row() -> None:
    """正規化在解析時就完成，下游拿到的一律是規範形狀。"""
    card = ExtractedCard(**MINIMAL, example="A dog. / 一隻狗。")

    assert CardRow(**card.to_card_fields()).example == "A dog.\n一隻狗。"


# ── tts_back_text 推導 ───────────────────────────────────────────


def test_empty_tts_back_text_is_derived_from_the_example() -> None:
    """實測 342 張有 2 張是空的，而 `audio_back` 沒有退路——那兩張直接失敗。"""
    card = ExtractedCard(**MINIMAL, tts_back_text="", example="毎日、嗽をする。\n每天漱口。")

    assert card.tts_back_text == "毎日、嗽をする。"


@pytest.mark.parametrize(
    "leaked",
    ["王女に仕える／侍奉公主。", "He works hard / 他很努力。"],
)
def test_tts_back_text_with_the_translation_is_replaced(leaked: str) -> None:
    """同一批有 5 張把譯文一起填進來——**會被整句唸出來**，而且有音檔所以不報錯。

    這比空著更糟：空的會失敗、看得到；這種是安靜地錯。
    """
    card = ExtractedCard(**MINIMAL, tts_back_text=leaked, example="王女に仕える\n侍奉公主。")

    assert card.tts_back_text == "王女に仕える"


def test_a_usable_value_is_kept_even_when_it_differs_from_the_example() -> None:
    """模型有時會順手清掉 OCR 留下的假名雜訊，那是加分，不要覆蓋掉。"""
    card = ExtractedCard(
        **MINIMAL, tts_back_text="目を通す", example="一応目を通す\n大致看一下。"
    )

    assert card.tts_back_text == "目を通す"


@pytest.mark.parametrize(("tts", "example"), [("", ""), ("", "   ")])
def test_nothing_to_derive_from_leaves_the_value_alone(tts: str, example: str) -> None:
    """規範就是「例句是空的話留空」。"""
    assert ExtractedCard(**MINIMAL, tts_back_text=tts, example=example).tts_back_text == tts


def test_derivation_helper_is_reusable_for_existing_work_files() -> None:
    """一次性修既有 CSV 時走的是同一個函式——兩份實作會漂移。"""
    assert derive_tts_back_text("", "王者\nKing of the country.") == "王者"
