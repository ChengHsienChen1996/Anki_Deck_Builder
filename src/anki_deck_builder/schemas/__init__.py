"""資料結構定義與驗證（型別層）。"""

from .card import (
    ENGINE_FIELDS,
    ENGINE_MANAGED_FIELDS,
    INTERMEDIATE_FIELDS,
    STATUS_FIELDS,
    CardRow,
)
from .extract_output import ExtractedCard, ExtractOutput
from .status import StageStatus

__all__ = [
    "ENGINE_FIELDS",
    "ENGINE_MANAGED_FIELDS",
    "INTERMEDIATE_FIELDS",
    "STATUS_FIELDS",
    "CardRow",
    "ExtractOutput",
    "ExtractedCard",
    "StageStatus",
]
