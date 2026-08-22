"""模型卸載單元測試（以 httpx MockTransport 攔截，不打任何真實服務）。"""

import httpx
import pytest

from anki_deck_builder.clients import model_unload
from anki_deck_builder.clients.model_unload import ollama_root, unload_model


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """輪詢間隔在測試中無意義，壓到 0 以免拖慢。"""
    monkeypatch.setattr(model_unload, "POLL_INTERVAL", 0)


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """把 AsyncClient 換成走 MockTransport 的版本，並記錄所有請求。"""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    original = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return original(*args, transport=httpx.MockTransport(record), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


def _ps(models: list[str]) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": name} for name in models]})


# ── base_url 還原 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "base_url",
    ["http://localhost:11434/v1/", "http://localhost:11434/v1", "http://localhost:11434/"],
)
def test_ollama_root_strips_openai_suffix(base_url: str) -> None:
    """keep_alive 只存在於原生 API，/v1 前綴必須去掉。"""
    assert ollama_root(base_url) == "http://localhost:11434"


# ── 正常路徑 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sends_keep_alive_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        return _ps([])

    seen = _install(monkeypatch, handler)

    assert await unload_model("http://localhost:11434/v1/", "glm-ocr:latest") is True

    generate = next(r for r in seen if r.url.path == "/api/generate")
    assert b'"keep_alive":0' in generate.content.replace(b" ", b"")


@pytest.mark.asyncio
async def test_polls_until_model_disappears(monkeypatch: pytest.MonkeyPatch) -> None:
    """卸載相對於回應是非同步的——回應回來時模型可能還在（實測 commit 90b91fb）。"""
    remaining = ["glm-ocr:latest", "glm-ocr:latest", ""]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        still = remaining.pop(0)
        return _ps([still] if still else [])

    seen = _install(monkeypatch, handler)

    assert await unload_model("http://localhost:11434/v1", "glm-ocr:latest") is True
    assert sum(1 for r in seen if r.url.path == "/api/ps") == 3


@pytest.mark.asyncio
async def test_wait_false_returns_without_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install(monkeypatch, lambda request: httpx.Response(200, json={}))

    assert await unload_model("http://localhost:11434/v1", "m", wait=False) is True
    assert not [r for r in seen if r.url.path == "/api/ps"]


@pytest.mark.asyncio
async def test_returns_false_when_still_loaded_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={})
        return _ps(["stuck:latest"])

    _install(monkeypatch, handler)

    stuck = await unload_model(
        "http://localhost:11434/v1", "stuck:latest", wait_timeout=0.05
    )
    assert stuck is False


# ── 失敗不拋例外 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_error_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama 沒開不該中斷流程——讓渡是最佳化，不是流程的一部分。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _install(monkeypatch, handler)

    assert await unload_model("http://localhost:11434/v1", "m") is False


@pytest.mark.asyncio
async def test_http_error_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: httpx.Response(404, json={"error": "not found"}))

    assert await unload_model("http://localhost:11434/v1", "m") is False
