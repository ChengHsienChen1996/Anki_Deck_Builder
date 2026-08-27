"""階段的組裝（流程層）。

「哪個階段要接哪個 client、client 要怎麼從設定建起來」在 Phase 1–4 寫在 `cli.py`，
因為當時只有一個介面。Phase 5 起 Web UI 也要跑同樣的階段，同一份組裝寫兩遍必然
會漂移（少接一個 `on_finish`、少一次 VRAM 讓渡，UI 的結果就與 CLI 不同）。
因此集中在這裡，`cli.py` 與 `web/` 都只呼叫本模組（約束 4）。

**匯入一律延後**：`agent_factory` 相依較重，`--help`、`status`、純打包都不該
為它付出啟動成本。
"""

from __future__ import annotations

from typing import Any

from ..config import Settings


def build_ocr_stage(settings: Settings) -> Any:
    """組出 OCR 階段，並依設定決定要不要接上 VRAM 讓渡。

    卸載是 Ollama 專屬手段，`agents.yaml` 可指向任何供應商，因此判斷放在組裝層，
    而不是寫死進階段或 client（architecture.md 的要求）。
    """
    from ..clients.model_unload import unload_model
    from ..clients.ocr_client import OCRClient
    from .ocr import OCRStage

    client = OCRClient(settings.agent_factory.yaml_settings_file)
    on_finish = None
    if settings.model_unload.enabled:
        base_url, model = client.model_endpoint()

        async def on_finish() -> bool:  # noqa: F811 - 只在啟用時定義
            return await unload_model(
                base_url, model, wait_timeout=settings.model_unload.timeout
            )

    return OCRStage(client, settings=settings, on_finish=on_finish)


def build_extract_stage(
    settings: Settings,
    deck_name: str | None = None,
    domain: str | None = None,
    source: str | None = None,
    card_id_prefix: str | None = None,
    card_language: str | None = None,
    deck_categories: str | None = None,
    enrich: bool | None = None,
) -> Any:
    """組出抽取階段。

    七個選填參數對應 CLI 的同名旗標；全部不給時階段自己退回設定值與自動判斷。
    Web UI 目前不提供這些欄位，走的就是全預設那條路。
    """
    from ..clients.llm_client import LLMClient
    from .extract import ExtractStage

    client = LLMClient(settings.agent_factory.yaml_settings_file)
    return ExtractStage(
        client,
        settings=settings,
        deck_name=deck_name,
        domain=domain,
        source=source,
        card_id_prefix=card_id_prefix,
        card_language=card_language,
        deck_categories=deck_categories,
        enrich=enrich,
    )


def build_image_stage(settings: Settings) -> Any:
    from ..clients.comfyui_client import ComfyUIClient
    from .image import ImageStage

    return ImageStage(ComfyUIClient(settings.comfyui), settings=settings)


def build_audio_stages(settings: Settings, side: str = "both") -> list[Any]:
    """依 `side` 組出要依序執行的 audio 階段。

    兩側是**兩個獨立階段**（`audio_front`／`audio_back`），共用同一個 client——
    模型建構要 77 秒，兩側各建一次等於白等一輪。
    """
    from ..clients.tts_client import VoxCPMClient
    from .audio import stages_for_side

    client = VoxCPMClient(settings.tts)
    return [stage_cls(client, settings=settings) for stage_cls in stages_for_side(side)]
