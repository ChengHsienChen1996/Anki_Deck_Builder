"""中間 CSV 的單列型別。

一份中間 CSV 貫穿五階段，同時是**資料載體**與**狀態機**（見 docs/architecture.md）。
欄位分三類，順序固定為 (a) → (b) → (c)：

- (a) 引擎 schema 欄位 23 個：打包後保留，與 Anki 記憶引擎的 CSV 格式一致
- (b) 製卡中間欄位 6 個：打包時移除
- (c) 狀態與錯誤欄位 10 個：打包時移除

`field_order()` 是欄位順序的**唯一真實來源**，其他模組不得自行列舉欄位——
順序一旦不一致，後續所有階段都會錯位。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .status import StageStatus

#: (a) 引擎 schema 欄位——打包後保留
ENGINE_FIELDS: tuple[str, ...] = (
    "card_id",
    "deck",
    "card_type",
    "front",
    "back",
    "hint",
    "image_front",
    "image_back",
    "audio_front",
    "audio_back",
    "video",
    "example",
    "mnemonic",
    "note",
    "tags",
    "category",
    "difficulty",
    "source",
    "created_at",
    "last_reviewed",
    "interval",
    "ease_factor",
    "review_count",
)

#: (b) 製卡中間欄位——打包時移除
INTERMEDIATE_FIELDS: tuple[str, ...] = (
    "raw_text",
    "ocr_source_page",
    "reading",
    "image_prompt",
    "tts_front_text",
    "tts_back_text",
)

#: (c) 狀態與錯誤欄位——打包時移除
STATUS_FIELDS: tuple[str, ...] = (
    "ocr_status",
    "ocr_error",
    "extract_status",
    "extract_error",
    "image_status",
    "image_error",
    "audio_front_status",
    "audio_front_error",
    "audio_back_status",
    "audio_back_error",
)

#: 由記憶引擎管理的欄位，本專案一律留空
ENGINE_MANAGED_FIELDS: tuple[str, ...] = (
    "created_at",
    "last_reviewed",
    "interval",
    "ease_factor",
    "review_count",
)


class CardRow(BaseModel):
    """中間 CSV 的一列。

    所有欄位都有預設值：字串預設空字串、狀態預設 `PENDING`、頁碼預設 `None`。
    Phase 1 不會填寫 `image_*`／`audio_*`／`tts_*` 等欄位，但現在就定義完整，
    後續 phase 只填值、不改結構。
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # ── (a) 引擎 schema 欄位 ──
    card_id: str = ""
    deck: str = ""
    card_type: str = ""
    front: str = ""
    back: str = ""
    hint: str = ""
    image_front: str = ""
    image_back: str = ""
    audio_front: str = ""
    audio_back: str = ""
    video: str = ""
    example: str = ""
    mnemonic: str = ""
    note: str = ""
    tags: str = ""
    category: str = ""
    difficulty: str = ""
    source: str = ""
    created_at: str = ""
    last_reviewed: str = ""
    interval: str = ""
    ease_factor: str = ""
    review_count: str = ""

    # ── (b) 製卡中間欄位 ──
    raw_text: str = ""
    ocr_source_page: int | None = None
    reading: str = ""
    image_prompt: str = ""
    tts_front_text: str = ""
    tts_back_text: str = ""

    # ── (c) 狀態與錯誤欄位 ──
    ocr_status: StageStatus = StageStatus.PENDING
    ocr_error: str = ""
    extract_status: StageStatus = StageStatus.PENDING
    extract_error: str = ""
    image_status: StageStatus = StageStatus.PENDING
    image_error: str = ""
    audio_front_status: StageStatus = StageStatus.PENDING
    audio_front_error: str = ""
    audio_back_status: StageStatus = StageStatus.PENDING
    audio_back_error: str = ""

    @classmethod
    def field_order(cls) -> tuple[str, ...]:
        """CSV 欄位順序的唯一真實來源：(a) → (b) → (c)，共 39 欄。"""
        return ENGINE_FIELDS + INTERMEDIATE_FIELDS + STATUS_FIELDS

    @classmethod
    def engine_field_order(cls) -> tuple[str, ...]:
        """打包後保留的 23 欄，供 `stages/pack.py` 還原引擎 schema。"""
        return ENGINE_FIELDS

    def to_csv_row(self) -> dict[str, str]:
        """轉為 CSV 可寫入的字串對應表，鍵的順序即 `field_order()`。"""
        values: dict[str, str] = {}
        for name in self.field_order():
            value = getattr(self, name)
            if value is None:
                values[name] = ""
            elif isinstance(value, StageStatus):
                values[name] = value.value
            else:
                values[name] = str(value)
        return values

    @classmethod
    def from_csv_row(cls, row: dict[str, str | None]) -> CardRow:
        """由 CSV 的一列建立實例。

        只取 `field_order()` 中的欄位，未知欄位忽略；空字串一律回退為該欄位的預設值，
        因此 `ocr_source_page` 為空時得到 `None` 而非轉型錯誤。
        """
        data: dict[str, Any] = {}
        for name in cls.field_order():
            raw = row.get(name)
            if raw is None or raw == "":
                continue
            data[name] = raw
        return cls(**data)
