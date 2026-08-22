"""五階段的流程邏輯（流程層）。

各階段模組於對應 phase 加入並以 `@register_stage` 註冊；`cli.py` 依名稱查表 dispatch。
"""

from .base import (
    BaseStage,
    StageResult,
    get_stage,
    register_stage,
    registered_stages,
)
from .extract import ExtractStage
from .input_source import InputKind, detect_input, is_image, list_images
from .pack import PackResult, pack

__all__ = [
    "BaseStage",
    "ExtractStage",
    "InputKind",
    "PackResult",
    "StageResult",
    "detect_input",
    "get_stage",
    "is_image",
    "list_images",
    "register_stage",
    "pack",
    "registered_stages",
]
