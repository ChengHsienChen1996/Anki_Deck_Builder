"""外部服務的封裝（服務層）。

具體實作於各 phase 加入：`llm_client`（Phase 1）、`ocr_client`（Phase 2）、
`comfyui_client`（Phase 3）、`tts_client`（Phase 4）。
"""

from .protocols import (
    AgentInput,
    ImageGenClientProtocol,
    LLMClientProtocol,
    OCRClientProtocol,
    TTSClientProtocol,
)

__all__ = [
    "AgentInput",
    "ImageGenClientProtocol",
    "LLMClientProtocol",
    "OCRClientProtocol",
    "TTSClientProtocol",
]
