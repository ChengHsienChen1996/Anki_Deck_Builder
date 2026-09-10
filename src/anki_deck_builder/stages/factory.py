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
    # 分塊預設關閉。開啟需要 `uv sync --all-extras`；關掉時整頁送，
    # 行為與 Phase 9 之前完全相同
    detector = None
    if settings.ocr_chunk.enabled:
        from ..clients.layout_detector import LayoutDetector

        detector = LayoutDetector(settings.ocr_chunk)
    on_finish = None
    if settings.model_unload.enabled:
        base_url, model = client.model_endpoint()

        async def on_finish() -> bool:  # noqa: F811 - 只在啟用時定義
            return await unload_model(
                base_url, model, wait_timeout=settings.model_unload.timeout
            )

    return OCRStage(client, settings=settings, on_finish=on_finish, detector=detector)


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


def build_scene_stage(settings: Settings) -> Any:
    """組出聯想圖場景階段（語義層）。

    與 extract 共用同一個 LLM client 類別與模型；本階段自己不需要任何專屬設定。
    """
    from ..clients.llm_client import LLMClient
    from .scene import SceneStage

    return SceneStage(LLMClient(settings.agent_factory.yaml_settings_file), settings=settings)


def build_prompt_stage(settings: Settings) -> Any:
    """組出聯想圖 prompt 階段（語法層）。

    用哪個 agent 由 `IMAGE_PROMPT_AGENT` 決定——那一行就是 profile 的開關。
    """
    from ..clients.llm_client import LLMClient
    from .prompt import PromptStage

    return PromptStage(LLMClient(settings.agent_factory.yaml_settings_file), settings=settings)


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


def build_verify_client(settings: Settings) -> tuple[Any, Any]:
    """組出核對所需的兩樣東西：LLM client 與版面偵測器。

    偵測器是**必要**的，不是最佳化——核對的塊必須與 OCR 當初讀的相同，
    沒有偵測器就沒有塊。因此關閉分塊時回傳 `None`，由呼叫端擋下並說明。
    """
    from ..clients.llm_client import LLMClient

    client = LLMClient(settings.agent_factory.yaml_settings_file)
    detector = None
    if settings.ocr_chunk.enabled:
        from ..clients.layout_detector import LayoutDetector

        detector = LayoutDetector(settings.ocr_chunk)
    return client, detector
