"""本地模型卸載：階段間的 VRAM 讓渡（服務層）。

抽取模型一個人就吃掉 20.3 GB／24 GB，`two_stage` 下 GLM-OCR 再佔 2.2 GB，
兩者相加 22.5 GB 已逼近上限（見執行計畫 §Q3）。Ollama 預設 `keep_alive` 為 5 分鐘，
不會主動讓位，因此階段結束後需要主動卸載。

**這是可選的收尾動作，不是 client 的一部分**（architecture.md 的要求）：
`agents.yaml` 可指向任何供應商，`keep_alive` 是 Ollama 專屬手段，寫死進
`llm_client`／`ocr_client` 會讓那些適配層綁死在單一供應商上。

## 卸載相對於回應是非同步的

實測（commit `90b91fb`）：`POST /api/generate` 帶 `keep_alive: 0` 回來的當下，
`/api/ps` 仍可能列出該模型。要確認真的讓出 VRAM，必須輪詢到清空為止——
**不能把回應當成完成訊號**。
"""

from __future__ import annotations

import asyncio
import time

import httpx

#: 輪詢 /api/ps 的間隔
POLL_INTERVAL = 0.2


def ollama_root(base_url: str) -> str:
    """把 OpenAI 相容的 base_url 還原成 Ollama 的原生根路徑。

    agent_factory 拿到的是 `http://localhost:11434/v1/`，但 `keep_alive`
    只存在於原生 API（`/api/generate`、`/api/ps`）。
    """
    return base_url.rstrip("/").removesuffix("/v1")


async def is_loaded(root: str, model: str, request_timeout: float = 5.0) -> bool:
    """該模型是否仍在 Ollama 的常駐清單中。"""
    async with httpx.AsyncClient(timeout=request_timeout) as client:
        response = await client.get(f"{root}/api/ps")
        response.raise_for_status()
        names = {item.get("name", "") for item in response.json().get("models", [])}
    return model in names


async def unload_model(
    base_url: str,
    model: str,
    wait_timeout: float = 30.0,
    wait: bool = True,
) -> bool:
    """請 Ollama 立刻卸載指定模型，釋放 VRAM。

    Args:
        base_url: agent 的 base_url（`.../v1` 會自動去除）。
        model: 模型名稱，例如 `glm-ocr-optimized:latest`。
        wait_timeout: 單次 HTTP 請求與輪詢等待的上限（秒）。
        wait: 是否輪詢 `/api/ps` 直到模型真的消失。關掉只送請求就返回。

    Returns:
        是否確認已卸載。`wait=False` 時只要請求成功即回 `True`。

    Note:
        **失敗不拋例外**——讓渡是最佳化，不是流程的一部分。Ollama 沒開、
        版本不支援 `keep_alive`、或模型早已卸載，都只該讓下一階段照常進行。
    """
    root = ollama_root(base_url)
    try:
        async with httpx.AsyncClient(timeout=wait_timeout) as client:
            response = await client.post(
                f"{root}/api/generate", json={"model": model, "keep_alive": 0}
            )
            response.raise_for_status()
    except (httpx.HTTPError, ValueError):
        return False

    if not wait:
        return True

    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        try:
            if not await is_loaded(root, model):
                return True
        except (httpx.HTTPError, ValueError):
            return False
        await asyncio.sleep(POLL_INTERVAL)
    return False
