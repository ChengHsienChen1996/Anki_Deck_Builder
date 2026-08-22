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
from .ocr import OCRStage, PrepareResult
from .pack import PackResult, pack
from .pdf_render import DEFAULT_DPI, render_pdf

__all__ = [
    "DEFAULT_DPI",
    "BaseStage",
    "ExtractStage",
    "InputKind",
    "OCRStage",
    "PackResult",
    "PrepareResult",
    "StageResult",
    "detect_input",
    "get_stage",
    "is_image",
    "list_images",
    "register_stage",
    "pack",
    "render_pdf",
    "registered_stages",
]
