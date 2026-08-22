"""LLM 呼叫的薄適配層（服務層）。

**這一層只做三件事**：建立並重用 agent_factory 的 factory、依名稱取 agent、
把外部例外轉成專案自訂例外。

以下**都不在這裡實作**——agent_factory 已經提供，重寫一份只會產生兩套行為：

| 能力 | 由誰提供 |
|------|----------|
| prompt 載入 | YAML 的 `instruction_file_path` |
| structured output 解析 | YAML 的 `output_schema` |
| 速率限制（TPM／RPM／RPD、預扣退款） | `LimitAgentRunner.run()` 內建 |
| 多供應商切換 | YAML 的 `client.base_url` / `model` |

也**不要**用 httpx 自己打 OpenAI-compatible endpoint——那會完全繞過速率限制。
"""

from __future__ import annotations

from pathlib import Path

from agent_factory.core import AgentFactory, create_agent_factory
from agent_factory.limit_runner import LimitAgentRunner
from pydantic import BaseModel

from ..exceptions import AnkiBuilderError, ConfigurationError, ExternalServiceError
from .agent_endpoint import agent_endpoint
from .protocols import AgentInput


class LLMClient:
    """實作 `LLMClientProtocol`。

    factory 與 runner 都建立一次後重用：前者要讀 YAML、驗證全部 agent 設定，
    後者持有該模型的速率限制器——每次呼叫重建會讓限流形同虛設。
    """

    def __init__(self, yaml_path: str | Path | None = None) -> None:
        """
        Args:
            yaml_path: `agents.yaml` 路徑。`None` 表示交由 agent_factory 讀取
                `YAML_SETTINGS_FILE` 環境變數。
        """
        self._yaml_path = Path(yaml_path) if yaml_path is not None else None
        self._factory: AgentFactory | None = None
        self._runners: dict[str, LimitAgentRunner] = {}

    async def run_agent(self, agent_name: str, input_: AgentInput) -> BaseModel:
        """以 `agents.yaml` 宣告的 agent 執行一次呼叫。

        Args:
            agent_name: YAML 中的 `name` 欄位值，例如 `"ExtractAgent"`。
            input_: 純文字，或影像 message list（`vision_direct`，Phase 2）。

        Returns:
            已依該 agent 的 `output_schema` 解析好的 Pydantic model。

        Raises:
            ConfigurationError: YAML 載入失敗，或找不到該 agent。
            ExternalServiceError: 呼叫失敗（逾時、速率限制、連線錯誤），
                或回傳值不是 structured output。
        """
        runner = self._get_runner(agent_name)
        try:
            result = await runner.run(input_=input_)
        except AnkiBuilderError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"agent {agent_name!r} 呼叫失敗：{type(exc).__name__}: {exc}"
            ) from exc

        output = result.final_output
        if not isinstance(output, BaseModel):
            raise ExternalServiceError(
                f"agent {agent_name!r} 未回傳 structured output（收到 "
                f"{type(output).__name__}）。請確認 agents.yaml 已設定 output_schema，"
                "且該模型支援 structured output。"
            )
        return output

    def model_endpoint(self, agent_name: str) -> tuple[str, str]:
        """回傳該 agent 的 `(base_url, 模型名)`，供階段間的 VRAM 讓渡使用。

        Raises:
            ConfigurationError: YAML 載入失敗、查無 agent，或取不到 base_url。
        """
        try:
            agent = self._get_factory().get_agent_by_name(agent_name)
        except KeyError as exc:
            raise ConfigurationError(f"agents.yaml 中找不到 agent {agent_name!r}") from exc
        return agent_endpoint(agent, agent_name)

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

    def _get_runner(self, agent_name: str) -> LimitAgentRunner:
        """取得（或建立）該 agent 的 runner。

        本方法全程同步、沒有 await，因此併發呼叫不會交錯，字典不需要額外加鎖。
        """
        runner = self._runners.get(agent_name)
        if runner is not None:
            return runner

        try:
            agent = self._get_factory().get_agent_by_name(agent_name)
        except KeyError as exc:
            raise ConfigurationError(
                f"agents.yaml 中找不到 agent {agent_name!r}"
            ) from exc

        runner = LimitAgentRunner(agent=agent)
        self._runners[agent_name] = runner
        return runner
