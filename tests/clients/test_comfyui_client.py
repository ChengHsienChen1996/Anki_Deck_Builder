"""ComfyUI client 單元測試。

HTTP 以 `httpx.MockTransport` 攔截、WebSocket 以假 socket 替換，
**不依賴 ComfyUI 實際啟動**。回應結構取自 2026-08-24 對 ComfyUI 0.28.3 的實測
（見 `comfyui_client` 模組 docstring）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import websockets

from anki_deck_builder.clients import comfyui_client
from anki_deck_builder.clients.comfyui_client import ComfyUIClient
from anki_deck_builder.config import ComfyUINodeSettings, ComfyUISettings
from anki_deck_builder.exceptions import ConfigurationError, ExternalServiceError

PROMPT_ID = "7cc909cf-4d1a-4da7-82a1-7da8a5840492"
PNG = b"\x89PNG\r\n\x1a\n fake"

WORKFLOW = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI"}},
}


# ── 測試替身 ─────────────────────────────────────────────────────


class FakeSocket:
    """依序吐出預錄訊息的假 WebSocket。訊息用完後模擬斷線。"""

    def __init__(self, messages: list[Any]) -> None:
        self._messages = list(messages)
        self.closed = False

    async def recv(self) -> Any:
        if not self._messages:
            raise websockets.ConnectionClosedError(None, None)
        message = self._messages.pop(0)
        if isinstance(message, Exception):
            raise message
        return message

    async def close(self) -> None:
        self.closed = True


class HangingSocket:
    """永遠等不到完成訊號的假 WebSocket，用來觸發整體逾時。"""

    def __init__(self) -> None:
        self.closed = False

    async def recv(self) -> Any:
        await asyncio.sleep(3600)
        raise AssertionError("不該走到這裡")  # pragma: no cover

    async def close(self) -> None:
        self.closed = True


def signal(kind: str, **data: Any) -> str:
    return json.dumps({"type": kind, "data": {"prompt_id": PROMPT_ID, **data}})


def install_socket(monkeypatch: pytest.MonkeyPatch, socket: Any) -> list[str]:
    """替換模組層的 `connect`，並記錄連線 URL。"""
    urls: list[str] = []

    async def fake_connect(url: str) -> Any:
        urls.append(url)
        if isinstance(socket, Exception):
            raise socket
        return socket

    monkeypatch.setattr(comfyui_client, "connect", fake_connect)
    return urls


def install_http(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
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


def make_settings(tmp_path, **overrides: Any) -> ComfyUISettings:
    """組出設定；**每個欄位都明給**，避免專案根目錄的 `.env` 滲進測試。

    `ComfyUISettings` 新增欄位時這裡要跟著補，否則開發機 `.env` 一設那個變數
    就會滲進來——Phase 7 的 `prompt_prefix` 與 `width_node_id` 都漏過。
    `monkeypatch.chdir()` 擋不住：`.env` 在 `agent_factory` 匯入時就被
    python-dotenv 灌進 `os.environ` 了。
    """
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(WORKFLOW), encoding="utf-8")
    values: dict[str, Any] = {
        "base_url": "http://comfy.test:8188",
        "workflow_path": path,
        "poll_interval": 0,
        "timeout": 30,
        "batch_size": 4,
        "image_width": 768,
        "image_height": 432,
        "negative_prompt": "text, watermark",
        "prompt_prefix": "",
        "free_before_llm": False,
        "nodes": ComfyUINodeSettings(
            positive_node_id="6",
            positive_field="text",
            negative_node_id="7",
            negative_field="text",
            seed_node_id="3",
            seed_field="seed",
            latent_node_id="5",
            width_node_id="",
            width_field="width",
            height_node_id="",
            height_field="height",
            output_node_id="9",
        ),
    }
    values.update(overrides)
    return ComfyUISettings(**values)


def history_body(
    outputs: dict | None = None, status_str: str = "success", completed: bool = True
) -> dict:
    return {
        PROMPT_ID: {
            "prompt": [],
            "outputs": outputs
            if outputs is not None
            else {"9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}},
            "status": {"status_str": status_str, "completed": completed, "messages": []},
        }
    }


def ok_handler(history: dict | None = None):
    """一路順利的 HTTP 回應。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID, "node_errors": {}})
        if request.url.path.startswith("/history/"):
            return httpx.Response(200, json=history if history is not None else history_body())
        if request.url.path == "/view":
            return httpx.Response(200, content=PNG)
        raise AssertionError(f"未預期的請求：{request.url}")

    return handler


@pytest.fixture
def client(tmp_path) -> ComfyUIClient:
    return ComfyUIClient(make_settings(tmp_path))


# ── URL 組合與 workflow ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://comfy.test:8188", "ws://comfy.test:8188"),
        ("http://comfy.test:8188/", "ws://comfy.test:8188"),
        ("https://comfy.test", "wss://comfy.test"),
    ],
)
def test_ws_url_follows_base_url(tmp_path, base_url: str, expected: str) -> None:
    assert ComfyUIClient(make_settings(tmp_path, base_url=base_url)).ws_url == expected


def test_validate_passes(client: ComfyUIClient) -> None:
    client.validate()


def test_validate_reports_bad_node(tmp_path) -> None:
    settings = make_settings(tmp_path)
    settings.nodes.positive_node_id = "99"

    with pytest.raises(ConfigurationError, match="COMFYUI_POSITIVE_NODE_ID=99"):
        ComfyUIClient(settings).validate()


def test_workflow_is_loaded_once(client: ComfyUIClient, tmp_path) -> None:
    first = client.load_workflow()
    (tmp_path / "wf.json").unlink()

    assert client.load_workflow() is first


# ── 正常流程 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_returns_png(monkeypatch, client: ComfyUIClient) -> None:
    socket = FakeSocket([signal("execution_success")])
    urls = install_socket(monkeypatch, socket)
    seen = install_http(monkeypatch, ok_handler())

    assert await client.generate("a tiger among cats", seed=42) == PNG

    assert urls[0].startswith("ws://comfy.test:8188/ws?clientId=")
    assert socket.closed
    assert [r.url.path for r in seen] == ["/prompt", f"/history/{PROMPT_ID}", "/view"]


@pytest.mark.asyncio
async def test_generate_injects_prompt_seed_and_size(monkeypatch, client) -> None:
    install_socket(monkeypatch, FakeSocket([signal("execution_success")]))
    seen = install_http(monkeypatch, ok_handler())

    await client.generate("a tiger among cats", seed=42)

    body = json.loads(seen[0].content)
    workflow = body["prompt"]
    assert body["client_id"]
    assert workflow["6"]["inputs"]["text"] == "a tiger among cats"
    assert workflow["7"]["inputs"]["text"] == "text, watermark"
    assert workflow["3"]["inputs"]["seed"] == 42
    assert (workflow["5"]["inputs"]["width"], workflow["5"]["inputs"]["height"]) == (768, 432)


@pytest.mark.asyncio
async def test_view_request_carries_image_reference(monkeypatch, client) -> None:
    history = history_body(
        {"9": {"images": [{"filename": "b.png", "subfolder": "sub", "type": "temp"}]}}
    )
    install_socket(monkeypatch, FakeSocket([signal("execution_success")]))
    seen = install_http(monkeypatch, ok_handler(history))

    await client.generate("p")

    view = seen[-1]
    assert dict(view.url.params) == {"filename": "b.png", "subfolder": "sub", "type": "temp"}


@pytest.mark.asyncio
async def test_accepts_legacy_executing_null_signal(monkeypatch, client) -> None:
    """舊版 ComfyUI 沒有 execution_success，只送 node 為 null 的 executing。"""
    install_socket(monkeypatch, FakeSocket([signal("executing", node=None)]))
    install_http(monkeypatch, ok_handler())

    assert await client.generate("p") == PNG


@pytest.mark.asyncio
async def test_ignores_noise_before_completion(monkeypatch, client) -> None:
    """進度訊息、預覽二進位幀、別人的任務都不該被當成完成。"""
    messages = [
        json.dumps({"type": "status", "data": {"status": {"exec_info": {}}}}),
        b"\x00\x00\x00\x01preview",
        signal("executing", node="4"),
        json.dumps({"type": "execution_success", "data": {"prompt_id": "別人的任務"}}),
        "這不是 JSON",
        signal("execution_success"),
    ]
    install_socket(monkeypatch, FakeSocket(messages))
    install_http(monkeypatch, ok_handler())

    assert await client.generate("p") == PNG


# ── WebSocket 退回輪詢 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_falls_back_to_polling_when_socket_unavailable(monkeypatch, client) -> None:
    """WS 連不上不該讓生成失敗——圖本來就從 /history + /view 取。"""
    install_socket(monkeypatch, OSError("connection refused"))
    seen = install_http(monkeypatch, ok_handler())

    assert await client.generate("p") == PNG
    assert [r.url.path for r in seen] == ["/prompt", f"/history/{PROMPT_ID}", "/view"]


@pytest.mark.asyncio
async def test_polls_history_until_completed(monkeypatch, client) -> None:
    """WS 中途斷線：輪詢 /history 直到 completed。"""
    install_socket(monkeypatch, FakeSocket([]))
    bodies = [
        {},
        history_body(outputs={}, completed=False),
        history_body(),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path.startswith("/history/"):
            return httpx.Response(200, json=bodies.pop(0) if len(bodies) > 1 else bodies[0])
        return httpx.Response(200, content=PNG)

    seen = install_http(monkeypatch, handler)

    assert await client.generate("p") == PNG
    assert sum(1 for r in seen if r.url.path.startswith("/history/")) == 3


# ── 失敗路徑 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execution_error_over_websocket(monkeypatch, client) -> None:
    install_socket(
        monkeypatch,
        FakeSocket(
            [
                signal(
                    "execution_error",
                    node_id="3",
                    exception_type="OutOfMemoryError",
                    exception_message="CUDA out of memory",
                )
            ]
        ),
    )
    install_http(monkeypatch, ok_handler())

    with pytest.raises(ExternalServiceError) as exc:
        await client.generate("p")

    assert "CUDA out of memory" in str(exc.value)
    assert "節點 3" in str(exc.value)


@pytest.mark.asyncio
async def test_error_status_in_history(monkeypatch, client) -> None:
    """WS 斷線時，/history 的 status_str 是唯一能看到錯誤的地方。"""
    history = {
        PROMPT_ID: {
            "outputs": {},
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [
                    [
                        "execution_error",
                        {
                            "node_id": "3",
                            "exception_type": "OutOfMemoryError",
                            "exception_message": "CUDA out of memory",
                        },
                    ]
                ],
            },
        }
    }
    install_socket(monkeypatch, FakeSocket([]))
    install_http(monkeypatch, ok_handler(history))

    with pytest.raises(ExternalServiceError, match="CUDA out of memory"):
        await client.generate("p")


@pytest.mark.asyncio
async def test_rejected_workflow_reports_node_errors(monkeypatch, client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "prompt outputs failed validation",
                                         "node_errors": {"3": "seed 超出範圍"}})

    install_socket(monkeypatch, FakeSocket([]))
    install_http(monkeypatch, handler)

    with pytest.raises(ExternalServiceError) as exc:
        await client.generate("p")

    assert "HTTP 400" in str(exc.value)
    assert "node_errors" in str(exc.value)
    assert "/prompt" in str(exc.value)


@pytest.mark.asyncio
async def test_history_server_error(monkeypatch, client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        return httpx.Response(500, text="internal error")

    install_socket(monkeypatch, FakeSocket([signal("execution_success")]))
    install_http(monkeypatch, handler)

    with pytest.raises(ExternalServiceError) as exc:
        await client.generate("p")

    assert "HTTP 500" in str(exc.value)
    assert f"/history/{PROMPT_ID}" in str(exc.value)


@pytest.mark.asyncio
async def test_missing_output_node_names_the_env_var(monkeypatch, client) -> None:
    """輸出節點設錯是最常見的設定錯誤，訊息要直接指出設了哪個、實際有哪些。"""
    history = history_body({"12": {"images": [{"filename": "a.png"}]}})
    install_socket(monkeypatch, FakeSocket([signal("execution_success")]))
    install_http(monkeypatch, ok_handler(history))

    with pytest.raises(ExternalServiceError) as exc:
        await client.generate("p")

    message = str(exc.value)
    assert "COMFYUI_OUTPUT_NODE_ID" in message
    assert "'9'" in message
    assert "12" in message


@pytest.mark.asyncio
async def test_output_node_without_images(monkeypatch, client) -> None:
    install_socket(monkeypatch, FakeSocket([signal("execution_success")]))
    install_http(monkeypatch, ok_handler(history_body({"9": {"images": []}})))

    with pytest.raises(ExternalServiceError, match="沒有產出圖片"):
        await client.generate("p")


@pytest.mark.asyncio
async def test_missing_prompt_id(monkeypatch, client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"node_errors": {}})

    install_socket(monkeypatch, FakeSocket([]))
    install_http(monkeypatch, handler)

    with pytest.raises(ExternalServiceError, match="沒有 prompt_id"):
        await client.generate("p")


@pytest.mark.asyncio
async def test_connection_failure(monkeypatch, client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    install_socket(monkeypatch, FakeSocket([]))
    install_http(monkeypatch, handler)

    with pytest.raises(ExternalServiceError) as exc:
        await client.generate("p")

    assert "連線失敗" in str(exc.value)
    assert "/prompt" in str(exc.value)


@pytest.mark.asyncio
async def test_empty_image_content(monkeypatch, client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path.startswith("/history/"):
            return httpx.Response(200, json=history_body())
        return httpx.Response(200, content=b"")

    install_socket(monkeypatch, FakeSocket([signal("execution_success")]))
    install_http(monkeypatch, handler)

    with pytest.raises(ExternalServiceError, match="空的圖片內容"):
        await client.generate("p")


@pytest.mark.asyncio
async def test_timeout_names_the_setting(monkeypatch, tmp_path) -> None:
    """逾時訊息要指名 COMFYUI_TIMEOUT，使用者才知道該調哪個變數。"""
    client = ComfyUIClient(make_settings(tmp_path, timeout=0))
    install_socket(monkeypatch, HangingSocket())
    install_http(monkeypatch, ok_handler())

    with pytest.raises(ExternalServiceError) as exc:
        await client.generate("p")

    assert "COMFYUI_TIMEOUT=0" in str(exc.value)


# ── 正向前綴 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_prepends_prompt_prefix(monkeypatch, tmp_path) -> None:
    """LoRA 觸發詞這類「模型要的」字串由設定帶入，卡片的 prompt 保持模型無關。"""
    client = ComfyUIClient(make_settings(tmp_path, prompt_prefix="Anime. "))
    install_socket(monkeypatch, FakeSocket([signal("execution_success")]))
    seen = install_http(monkeypatch, ok_handler())

    await client.generate("a tiger among cats")

    workflow = json.loads(seen[0].content)["prompt"]
    assert workflow["6"]["inputs"]["text"] == "Anime. a tiger among cats"


@pytest.mark.asyncio
async def test_generate_without_prefix_sends_prompt_verbatim(monkeypatch, client) -> None:
    """預設空字串——既有設定送出的正向 prompt 必須一字不差。"""
    install_socket(monkeypatch, FakeSocket([signal("execution_success")]))
    seen = install_http(monkeypatch, ok_handler())

    await client.generate("a tiger among cats")

    workflow = json.loads(seen[0].content)["prompt"]
    assert workflow["6"]["inputs"]["text"] == "a tiger among cats"


@pytest.mark.asyncio
async def test_prompt_prefix_is_used_verbatim(monkeypatch, tmp_path) -> None:
    """前綴原樣相接，不代插空白——分隔符屬於設定值的一部分。"""
    client = ComfyUIClient(make_settings(tmp_path, prompt_prefix="Anime."))
    install_socket(monkeypatch, FakeSocket([signal("execution_success")]))
    seen = install_http(monkeypatch, ok_handler())

    await client.generate("a tiger")

    workflow = json.loads(seen[0].content)["prompt"]
    assert workflow["6"]["inputs"]["text"] == "Anime.a tiger"
