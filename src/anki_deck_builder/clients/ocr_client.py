"""OCR 呼叫的薄適配層（服務層）。

與 `llm_client` 同一套結構——OCR 走的是**同一個 agent_factory**，只是 `agents.yaml`
指向 GLM-OCR 模型。這裡不裝任何 OCR 套件、不自己打 HTTP：繞過 `LimitAgentRunner`
就等於繞過速率限制。

**影像以 base64 傳入**（`OCRClientProtocol` 已定案）：避開路徑中的非 ASCII 字元、
空白與跨平台分隔符，且 agent_factory 只有拿到 data URL 才估得出影像 token
（遠端 URL 一律保守高估）。

## 編碼前必須縮圖

GLM-OCR 宣告的 `max_pixels` 為 9,633,792。實測 `tests/fixtures/pages/` 的書頁照片為
4000x3000 = 12M px，**直接送會超標**；而 DPI 並不足以框住像素數——同樣 DPI 200，
真 A4 畫布渲染出 3.87M px，手機照片轉的 PDF 卻是 12M px（見執行計畫 §2.4）。
因此上限在這一層強制，而不是靠調 DPI。
"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path

from agent_factory.core import AgentFactory, create_agent_factory
from agent_factory.limit_runner import LimitAgentRunner

from ..exceptions import AnkiBuilderError, ConfigurationError, ExternalServiceError, WorkFileError

#: `agents.yaml` 中的 OCR agent 名稱
OCR_AGENT_NAME = "OCRAgent"

#: GLM-OCR 宣告的像素上限（`glmocr.vision.max_pixels`）。留 5% 餘裕避免邊界爭議
MAX_PIXELS = 9_633_792
PIXEL_SAFETY_RATIO = 0.95

#: 縮圖後的重新編碼格式。JPEG 對書頁照片的體積優勢明顯，OCR 不需要無損
RESAMPLE_FORMAT = "JPEG"
RESAMPLE_QUALITY = 92

#: 送進 agent 的 data URL 預設 MIME。呼叫端若自行編碼 PNG 需一併指定
DEFAULT_MIME = "image/jpeg"


def _fit_pixels(raw: bytes, max_pixels: int) -> tuple[bytes, str]:
    """像素數超過上限時等比縮圖，否則原樣回傳。

    Pillow 已是 `voxcpm` 的既有相依，不算為此新增套件。
    """
    from PIL import Image, UnidentifiedImageError

    budget = int(max_pixels * PIXEL_SAFETY_RATIO)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if width * height <= budget:
                return raw, ""
            # 等比縮放：新邊長 = 原邊長 * sqrt(預算 / 目前像素數)
            ratio = (budget / (width * height)) ** 0.5
            target = (max(1, int(width * ratio)), max(1, int(height * ratio)))
            resized = image.convert("RGB")
            resized.thumbnail(target, Image.LANCZOS)
            buffer = io.BytesIO()
            resized.save(buffer, format=RESAMPLE_FORMAT, quality=RESAMPLE_QUALITY)
    except UnidentifiedImageError as error:
        raise WorkFileError(f"無法辨識的影像格式（{error}）") from error

    return buffer.getvalue(), DEFAULT_MIME


def _encode_sync(path: Path, max_pixels: int) -> str:
    """讀檔、必要時縮圖、轉 base64（同步，由 `encode_image_b64` 丟到執行緒跑）。"""
    if not path.is_file():
        raise WorkFileError(f"影像不存在：{path}")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise WorkFileError(f"影像讀取失敗：{path}（{error}）") from error

    if not raw:
        raise WorkFileError(f"影像是空檔案：{path}")

    try:
        fitted, _ = _fit_pixels(raw, max_pixels)
    except WorkFileError as error:
        raise WorkFileError(f"{error}：{path}") from error

    return base64.b64encode(fitted).decode("ascii")


async def encode_image_b64(path: str | Path, max_pixels: int = MAX_PIXELS) -> str:
    """把影像檔讀成 base64 字串，超過像素上限時先等比縮圖。

    Args:
        path: 影像路徑。
        max_pixels: 像素上限，預設為 GLM-OCR 的 `max_pixels`。

    Returns:
        base64 內容（**不含** `data:` 前綴，與 `OCRClientProtocol` 一致）。

    Raises:
        WorkFileError: 檔案不存在、讀取失敗、是空檔，或不是可辨識的影像。
    """
    return await asyncio.to_thread(_encode_sync, Path(path), max_pixels)


def build_image_input(image_b64: str, mime: str = DEFAULT_MIME) -> list[dict[str, object]]:
    """組出 agent_factory 的多模態 input（影像訊息 + 任務訊息）。

    格式取自 submodule 的參考實作 `src/agent_factory/tests/test_multimodal.py`，
    該測試用的正是本專案的 `glm-ocr-optimized:latest`。

    任務前綴（`Text Recognition:`）由 `agents.yaml` 的 `instruction_file_path`
    帶入，此處**不重複送**——GLM-OCR 是 prompt-limited 模型，多送一份自擬文字
    會讓它退化。
    """
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": f"data:{mime};base64,{image_b64}",
                }
            ],
        }
    ]


class OCRClient:
    """實作 `OCRClientProtocol`。

    factory 與 runner 建立一次後重用，理由同 `LLMClient`：前者要讀 YAML 並驗證
    全部 agent 設定，後者持有該模型的速率限制器。
    """

    def __init__(
        self,
        yaml_path: str | Path | None = None,
        agent_name: str = OCR_AGENT_NAME,
    ) -> None:
        """
        Args:
            yaml_path: `agents.yaml` 路徑。`None` 表示交由 agent_factory 讀取
                `YAML_SETTINGS_FILE` 環境變數。
            agent_name: YAML 中的 OCR agent 名稱。
        """
        self._yaml_path = Path(yaml_path) if yaml_path is not None else None
        self._agent_name = agent_name
        self._factory: AgentFactory | None = None
        self._runner: LimitAgentRunner | None = None

    async def recognize(self, image_b64: str) -> str:
        """辨識單張影像，回傳純文字。

        Args:
            image_b64: 影像的 base64 內容（不含 `data:` 前綴）。

        Returns:
            辨識出的文字，前後空白已去除。

        Raises:
            ConfigurationError: YAML 載入失敗，或查無該 agent。
            ExternalServiceError: 呼叫失敗，或回傳空結果。
        """
        if not image_b64:
            raise ExternalServiceError("影像內容為空，無法辨識")

        runner = self._get_runner()
        try:
            result = await runner.run(input_=build_image_input(image_b64))
        except AnkiBuilderError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"agent {self._agent_name!r} 辨識失敗：{type(exc).__name__}: {exc}"
            ) from exc

        text = str(result.final_output or "").strip()
        if not text:
            raise ExternalServiceError(
                f"agent {self._agent_name!r} 回傳空結果。"
                "可能是影像無法辨識，或該模型的 max_tokens 被思考內容耗盡。"
            )
        return text

    def model_endpoint(self) -> tuple[str, str]:
        """回傳該 agent 的 `(base_url, 模型名)`，供階段間的 VRAM 讓渡使用。

        兩者都從 agent 物件推得，**不在 `.env` 另行宣告**——endpoint 與模型名的
        唯一真實來源是 `agents.yaml`（見 architecture.md）。

        Raises:
            ConfigurationError: YAML 載入失敗、查無 agent，或該 agent 的 model
                物件不是 OpenAI 相容型態（取不到 base_url）。
        """
        try:
            agent = self._get_factory().get_agent_by_name(self._agent_name)
        except KeyError as exc:
            raise ConfigurationError(f"agents.yaml 中找不到 agent {self._agent_name!r}") from exc

        model = getattr(agent.model, "model", None)
        client = getattr(agent.model, "_client", None) or getattr(
            agent.model, "openai_client", None
        )
        base_url = getattr(client, "base_url", None)
        if not model or not base_url:
            raise ConfigurationError(
                f"agent {self._agent_name!r} 的模型物件取不到 base_url 或模型名"
                "（非 OpenAI 相容供應商？）"
            )
        return str(base_url), str(model)

    def _get_factory(self) -> AgentFactory:
        if self._factory is None:
            try:
                self._factory = (
                    AgentFactory.create_factory_from_yaml(self._yaml_path)
                    if self._yaml_path is not None
                    else create_agent_factory()
                )
            except Exception as exc:
                raise ConfigurationError(
                    f"載入 agents.yaml 失敗：{type(exc).__name__}: {exc}"
                ) from exc
        return self._factory

    def _get_runner(self) -> LimitAgentRunner:
        """取得（或建立）OCR agent 的 runner。全程同步、無 await，不需加鎖。"""
        if self._runner is not None:
            return self._runner

        try:
            agent = self._get_factory().get_agent_by_name(self._agent_name)
        except KeyError as exc:
            raise ConfigurationError(f"agents.yaml 中找不到 agent {self._agent_name!r}") from exc

        self._runner = LimitAgentRunner(agent=agent)
        return self._runner
