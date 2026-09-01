"""ExtractOutput / ExtractedCard 單元測試。"""

import pytest
from pydantic import ValidationError

from anki_deck_builder.schemas import CardRow, ExtractedCard, ExtractOutput

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
