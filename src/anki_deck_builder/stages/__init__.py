"""各階段的流程邏輯（流程層）。

各階段模組於對應 phase 加入並以 `@register_stage` 註冊；`cli.py` 依名稱查表 dispatch。
"""

from .audio import AudioBackStage, AudioFrontStage, stages_for_side
from .base import (
    BaseStage,
    StageResult,
    get_stage,
    register_stage,
    registered_stages,
)
from .extract import ExtractStage
from .image import ImageStage, stable_seed
from .input_source import InputKind, detect_input, is_image, list_images
from .ocr import OCRStage, PrepareResult
from .pack import PackResult, pack
from .pdf_render import DEFAULT_DPI, render_pdf
from .progress import ProgressReporter, format_duration
from .scene import SceneStage

__all__ = [
    "DEFAULT_DPI",
    "AudioBackStage",
    "AudioFrontStage",
    "BaseStage",
    "ExtractStage",
    "ImageStage",
    "InputKind",
    "OCRStage",
    "PackResult",
    "PrepareResult",
    "SceneStage",
    "ProgressReporter",
    "StageResult",
    "detect_input",
    "format_duration",
    "get_stage",
    "is_image",
    "list_images",
    "register_stage",
    "pack",
    "render_pdf",
    "stable_seed",
    "stages_for_side",
    "registered_stages",
]
