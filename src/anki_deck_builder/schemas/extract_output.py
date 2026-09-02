"""階段 ② 的 LLM structured output schema。

由 `agents.yaml` 的 `output_schema` 以 dotted path 引用：

    output_schema: anki_deck_builder.schemas.extract_output.ExtractOutput

agent_factory 會依此自動把回傳值解析為 `ExtractOutput`，本專案不自行解析 JSON。

**一頁文字對多張卡**：中間 CSV 的一列存的是一段 `raw_text`（通常是一整頁），
一頁可切出多個詞條，因此回傳值是 `cards` 清單而非單張卡。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from .card import CardRow

#: `example` 裡分隔原文與譯文的兩種寫法。與 `_normalise_example_separator` 認的同一組
_TRANSLATION_SEPARATORS: tuple[str, ...] = ("／", " / ")


def derive_tts_back_text(tts_back_text: str, example: str) -> str:
    """`tts_back_text` 不可用時，改由 `example` 的原文例句推導。

    規範是「`example` 中的原文例句，不含譯文」——也就是 `example` 的第一行。
    **這是確定性的字串操作**，交給模型的服從度去做每批都會漏幾張
    （理由同 `_normalise_example_separator`）。

    只在模型的值**不可用**時才動手，不是無條件覆蓋：

    - **空字串**：2026-09-02 實測 342 張有 2 張，`audio_back` 沒有退路因此直接失敗
    - **混進譯文**：同一批有 5 張是 `原文／譯文` 的完整字串，**會被整句唸出來**
      （日文例句後面接一段中文）。這些有產出音檔所以不會報錯，是安靜地錯

    其餘一律保留模型的值——它有時會順手清掉 OCR 留下的假名雜訊，那是加分。
    `example` 為空時也保留原值（規範就是「例句是空的話留空」）。
    """
    current = tts_back_text.strip()
    head = example.strip().splitlines()[0].strip() if example.strip() else ""
    if not head:
        return tts_back_text

    unusable = not current or any(sep in current for sep in _TRANSLATION_SEPARATORS)
    return head if unusable else tts_back_text


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

    @field_validator("example", mode="after")
    @classmethod
    def _normalise_example_separator(cls, value: str) -> str:
        """把 `原文／譯文` 正規化成 `原文\n譯文`。

        **這是實測補的，不是預防性程式碼。** 2026-09-01 掃描實產的 308 張卡：
        176 張（57%）用 `／` 或 ` / ` 而不是規定的換行，而且退化是分頁單調的
        （p1 合規 102/102、p2 30/91、p3 **0/115**）。prompt 早就寫了
        「原文以斜線 ／ 分隔例句與譯文時，換成上述格式」，模型就是沒照做。

        分隔符轉換是**確定性的字串操作**，用規則做一次就穩；靠模型服從度做，
        每跑一批就重擲一次骰子。所以這件事從 prompt 移到程式，
        prompt 那條規則留著當第一道（模型做對就不必動用這裡）。

        只在**沒有換行**時才動手：已經合規的一個字都不碰。找不到分隔符也原樣返回
        ——例句本來就可能只有原文沒有譯文，那不是錯誤。

        **只認全形 `／` 與前後有空格的 ` / `**，不認裸的 `/`：實測資料用的就是這兩種，
        而裸斜線會把 `I like a/b testing.` 這種句中斜線誤切（開發時實際踩到）。
        殘留風險是 `He works 9 / 5.` 這類句子仍會被切開——比起讓 57% 的卡片
        格式不一致，這個代價可以接受。
        """
        text = value.strip()
        if not text or "\n" in text:
            return value

        for separator in ("／", " / "):
            head, found, tail = text.partition(separator)
            if found and head.strip() and tail.strip():
                return f"{head.strip()}\n{tail.strip()}"
        return value

    @model_validator(mode="after")
    def _fill_tts_back_text(self) -> ExtractedCard:
        """`tts_back_text` 空著或混進譯文時，改由 `example` 推導。

        寫成 model validator 而非 field validator，因為它要同時看兩個欄位；
        `example` 的分隔符正規化是 field validator，會先跑完。
        """
        fixed = derive_tts_back_text(self.tts_back_text, self.example)
        if fixed != self.tts_back_text:
            object.__setattr__(self, "tts_back_text", fixed)
        return self

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
