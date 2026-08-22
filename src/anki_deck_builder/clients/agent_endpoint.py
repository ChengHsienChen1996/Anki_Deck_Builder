"""從 agent 物件推導 endpoint 與模型名（服務層）。

VRAM 讓渡需要知道「要卸載誰、要留下誰」，而 endpoint 與模型名的**唯一真實來源
是 `agents.yaml`**（architecture.md）。與其在 `.env` 另行宣告一份、承擔兩邊不同步
的風險，不如直接從 agent_factory 建好的 agent 物件讀回來。
"""

from __future__ import annotations

from typing import Any

from ..exceptions import ConfigurationError


def agent_endpoint(agent: Any, agent_name: str) -> tuple[str, str]:
    """回傳 `(base_url, 模型名)`。

    Raises:
        ConfigurationError: 該 agent 的 model 物件不是 OpenAI 相容型態
            （取不到 base_url），例如換成了其他供應商的實作。
    """
    model = getattr(agent.model, "model", None)
    client = getattr(agent.model, "_client", None) or getattr(agent.model, "openai_client", None)
    base_url = getattr(client, "base_url", None)
    if not model or not base_url:
        raise ConfigurationError(
            f"agent {agent_name!r} 的模型物件取不到 base_url 或模型名（非 OpenAI 相容供應商？）"
        )
    return str(base_url), str(model)
