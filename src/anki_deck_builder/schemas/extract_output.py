"""階段 ② 的 LLM structured output schema。

由 `agents.yaml` 的 `output_schema` 以 dotted path 引用：

    output_schema: anki_deck_builder.schemas.extract_output.ExtractOutput

agent_factory 會依此自動把回傳值解析為 `ExtractOutput`，本專案不自行解析 JSON。

**一頁文字對多張卡**：中間 CSV 的一列存的是一段 `raw_text`（通常是一整頁），
一頁可切出多個詞條，因此回傳值是 `cards` 清單而非單張卡。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .card import CardRow


class ExtractedCard(BaseModel):
    """LLM 為單一詞條產出的內容。

    對應中間 CSV 的 (a) 引擎欄位中由 extract 填寫的部分，加上 (b) 的三個欄位。
    系統欄位（`created_at` 至 `review_count`）不在此處——它們一律留空，由記憶引擎管理。

    **不含 `image_scene`／`image_prompt`**：聯想圖的語義與語法自 Phase 8 起
    各自成階段（`scene`、`prompt`）。理由見 `stages/scene.py` 的模組 docstring——
    8B 模型在同一次呼叫裡做結構化填表與創意視覺轉譯會顧此失彼。
    """

    card_id: str = Field(description="牌組內唯一的卡片識別碼")
    deck: str = Field(description="牌組名稱，階層以 :: 分隔")
    card_type: str = Field(default="basic", description="卡片型別")
    front: str = Field(description="正面：詞條本身")
    back: str = Field(description="背面：釋義，多義項以「；」分隔")
    hint: str = Field(default="", description="提示，通常放詞性標記")
    example: str = Field(default="", description="例句，原文與翻譯以 \\n 分隔")
    mnemonic: str = Field(default="", description="記憶法／聯想說明")
    note: str = Field(default="", description="補充說明，如接續形式")
    tags: str = Field(default="", description="標籤，以空白分隔")
    category: str = Field(default="", description="分類")
    difficulty: int = Field(default=3, description="難度")
    source: str = Field(default="", description="來源，如書名與頁碼")
    reading: str = Field(default="", description="讀音（假名／拼音），供 TTS 使用")
    tts_front_text: str = Field(default="", description="audio_front 要唸的文字")
    tts_back_text: str = Field(default="", description="audio_back 要唸的文字")

    def to_card_fields(self) -> dict[str, str]:
        """轉為 `CardRow` 可直接套用的欄位對應表。

        欄位映射集中於此，避免 `stages/extract.py` 自行列舉欄位而與 `CardRow` 脫節。
        """
        fields = {name: str(value) for name, value in self.model_dump().items()}
        unknown = set(fields) - set(CardRow.field_order())
        if unknown:  # pragma: no cover - 僅在兩邊 schema 脫節時觸發
            raise ValueError(f"ExtractedCard 含 CardRow 沒有的欄位：{sorted(unknown)}")
        return fields


class ExtractOutput(BaseModel):
    """單次 extract 呼叫的完整回傳：一段 `raw_text` 切出的所有卡片。"""

    cards: list[ExtractedCard] = Field(default_factory=list, description="抽取出的所有卡片")
