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

__all__ = [
    "BaseStage",
    "StageResult",
    "get_stage",
    "register_stage",
    "registered_stages",
]
