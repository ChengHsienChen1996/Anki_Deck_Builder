"""OCR 呼叫的薄適配層（服務層）。

與 `llm_client` 同一套結構——OCR 走的是**同一個 agent_factory**，只是 `agents.yaml`
指向 GLM-OCR 模型。這裡不裝任何 OCR 套件、不自己打 HTTP：繞過 `LimitAgentRunner`
就等於繞過速率限制。

影像的編碼與縮圖規則與 `vision_direct` 路徑共用，實作在 `image_input`。
"""

from __future__ import annotations

from pathlib import Path

from agent_factory.core import AgentFactory, create_agent_factory
from agent_factory.limit_runner import LimitAgentRunner

from ..exceptions import AnkiBuilderError, ConfigurationError, ExternalServiceError
from .image_input import (
    DEFAULT_MIME,
    MAX_PIXELS,
    PIXEL_SAFETY_RATIO,
    build_image_input,
    encode_image_b64,
)

#: `agents.yaml` 中的 OCR agent 名稱
OCR_AGENT_NAME = "OCRAgent"

#: 從 `image_input` 轉出，讓既有匯入路徑維持可用
__all__ = [
    "DEFAULT_MIME",
    "MAX_PIXELS",
    "OCR_AGENT_NAME",
    "PIXEL_SAFETY_RATIO",
    "OCRClient",
    "build_image_input",
    "encode_image_b64",
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
