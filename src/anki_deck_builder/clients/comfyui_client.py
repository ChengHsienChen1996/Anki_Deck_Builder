"""ComfyUI 聯想圖生成（服務層）。

實作 `ImageGenClientProtocol`，走官方 API 範例的**方法 2（WebSocket + History）**：

    連 WebSocket → POST /prompt → 等完成訊號 → GET /history/{id} → GET /view

## WebSocket 只用來等完成，圖仍走 HTTP

方法 1（純輪詢 `/history`）每張圖平均要多等半個輪詢間隔，`COMFYUI_POLL_INTERVAL`
預設 2 秒、一副牌上百張，光空等就是好幾分鐘。方法 3（`SaveImageWebsocket`）雖然
省下一次 HTTP，但要求使用者改自己的 workflow 換掉輸出節點——與「workflow 由使用者
自帶、程式不假設結構」的約束衝突，因此不採用。

**先連線再送出**：反過來會漏掉在連線建立前就送達的完成訊息（512px 的 SD 1.5
幾秒鐘就跑完，這不是理論問題）。

## WebSocket 斷了不影響正確性

完成訊號只是「可以去拿圖了」的提示，圖片本體一律從 `/history` + `/view` 取得。
因此 WS 連不上或中途斷線時退回輪詢 `/history`（`COMFYUI_POLL_INTERVAL`），
只是慢一點，不會失敗。

## 實測回應結構（ComfyUI 0.28.3，2026-08-24）

```
POST /prompt        → {"prompt_id": "...", "number": 0, "node_errors": {}}
WS  完成訊號         → {"type": "execution_success", "data": {"prompt_id": "..."}}
                      隨後 {"type": "executing", "data": {"node": null, "prompt_id": "..."}}
GET /history/{id}   → {"<id>": {"prompt": …, "outputs": {"9": {"images": [
                          {"filename": "ComfyUI_00002_.png", "subfolder": "", "type": "output"}
                      ]}}, "status": {"status_str": "success", "completed": true, …}}}
GET /view           → image/png bytes
```

兩種完成訊號都接受：`execution_success` 是較新版本才有的，舊版只送
`executing` 且 `node` 為 `null`。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import websockets

from ..config import ComfyUISettings
from ..exceptions import ExternalServiceError
from . import workflow as workflow_module

#: WebSocket 連線函式。測試以 monkeypatch 替換，不打真實服務
connect = websockets.connect

#: 單次 HTTP 請求的上限。整體時間由 `COMFYUI_TIMEOUT` 控制，這裡只擋住單一請求卡死
REQUEST_TIMEOUT = 30.0


class ComfyUIClient:
    """ComfyUI 的 HTTP／WebSocket 適配層。

    負向 prompt 與尺寸不進 `generate()` 的簽章——它們對整批一致，由本類別
    自設定取得（見 `ImageGenClientProtocol`）。
    """

    def __init__(self, settings: ComfyUISettings) -> None:
        self._settings = settings
        self._workflow: dict[str, Any] | None = None

    # ── 設定與 workflow ──────────────────────────────────────────

    @property
    def base_url(self) -> str:
        return self._settings.base_url.rstrip("/")

    @property
    def ws_url(self) -> str:
        base = self.base_url
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :]
        return "ws://" + base.removeprefix("http://")

    def load_workflow(self) -> dict[str, Any]:
        """載入並快取 workflow。上百張卡共用同一份，不必重讀檔案。"""
        if self._workflow is None:
            self._workflow = workflow_module.load_workflow(self._settings.workflow_path)
        return self._workflow

    def validate(self) -> None:
        """階段開始前的設定檢查，由 `ImageStage.validate_settings()` 呼叫。

        Raises:
            ConfigurationError: workflow 讀不到，或注入點對不上。
        """
        workflow_module.validate(self.load_workflow(), self._settings.nodes)

    # ── 生成 ─────────────────────────────────────────────────────

    async def generate(self, positive_prompt: str, seed: int | None = None) -> bytes:
        """生成一張圖，回傳 PNG bytes。

        Args:
            positive_prompt: 該列的 `image_prompt`。
            seed: 亂數種子；`None` 或未設定 seed 節點時交由 workflow 決定。

        Raises:
            ConfigurationError: workflow 或注入點設定有問題。
            ExternalServiceError: 提交失敗、執行錯誤、逾時，或回應中找不到輸出。
        """
        payload = workflow_module.inject(
            self.load_workflow(),
            self._settings.nodes,
            positive=positive_prompt,
            negative=self._settings.negative_prompt,
            seed=seed,
            width=self._settings.image_width,
            height=self._settings.image_height,
        )
        try:
            async with asyncio.timeout(self._settings.timeout):
                return await self._generate(payload)
        except TimeoutError as error:
            raise ExternalServiceError(
                f"ComfyUI 生成逾時（超過 COMFYUI_TIMEOUT={self._settings.timeout} 秒）："
                f"{self.base_url}"
            ) from error

    async def _generate(self, payload: dict[str, Any]) -> bytes:
        client_id = uuid.uuid4().hex
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as http:
            socket = await self._open_socket(client_id)
            try:
                prompt_id = await self._submit(http, payload, client_id)
                if socket is not None:
                    await self._await_signal(socket, prompt_id)
            finally:
                if socket is not None:
                    await socket.close()

            entry = await self._fetch_entry(http, prompt_id)
            return await self._download(http, self._image_ref(entry, prompt_id))

    async def _open_socket(self, client_id: str) -> Any | None:
        """連上 WebSocket。連不上回 `None`——退回輪詢即可，不該讓生成失敗。"""
        try:
            return await connect(f"{self.ws_url}/ws?clientId={client_id}")
        except (OSError, websockets.WebSocketException):
            return None

    async def _submit(
        self, http: httpx.AsyncClient, payload: dict[str, Any], client_id: str
    ) -> str:
        """送出 workflow，回傳 `prompt_id`。"""
        endpoint = f"{self.base_url}/prompt"
        try:
            response = await http.post(
                endpoint, json={"prompt": payload, "client_id": client_id}
            )
        except httpx.HTTPError as error:
            raise ExternalServiceError(f"ComfyUI 連線失敗：{endpoint}（{error}）") from error

        if response.status_code >= 400:
            raise ExternalServiceError(
                f"ComfyUI 拒絕這份 workflow（HTTP {response.status_code}）：{endpoint} "
                f"{_body_excerpt(response)}"
            )

        prompt_id = _json(response, endpoint).get("prompt_id")
        if not prompt_id:
            raise ExternalServiceError(f"ComfyUI 回應中沒有 prompt_id：{endpoint}")
        return str(prompt_id)

    async def _await_signal(self, socket: Any, prompt_id: str) -> None:
        """等到這個 `prompt_id` 執行完畢。

        只認自己這一筆——同一個 ComfyUI 可能有別人的任務在跑。斷線不算失敗，
        直接返回讓 `_fetch_entry()` 輪詢接手。
        """
        while True:
            try:
                message = await socket.recv()
            except websockets.WebSocketException:
                return

            if isinstance(message, bytes | bytearray):
                # 預覽圖的二進位幀，與完成與否無關
                continue
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                continue

            data = event.get("data") or {}
            if data.get("prompt_id") != prompt_id:
                continue

            kind = event.get("type")
            if kind == "execution_error":
                raise ExternalServiceError(
                    f"ComfyUI 執行失敗（節點 {data.get('node_id')}）："
                    f"{data.get('exception_type', '')} {data.get('exception_message', '')}".strip()
                )
            if kind == "execution_success":
                return
            if kind == "executing" and data.get("node") is None:
                return

    async def _fetch_entry(self, http: httpx.AsyncClient, prompt_id: str) -> dict[str, Any]:
        """取這一筆的歷史紀錄。

        WS 正常時第一次就取得；WS 連不上或斷線時，這裡的重試就是輪詢後備。
        外層的 `COMFYUI_TIMEOUT` 會在整體逾時中止，不需要另設次數上限。
        """
        endpoint = f"{self.base_url}/history/{prompt_id}"
        while True:
            try:
                response = await http.get(endpoint)
            except httpx.HTTPError as error:
                raise ExternalServiceError(f"ComfyUI 連線失敗：{endpoint}（{error}）") from error

            if response.status_code >= 400:
                raise ExternalServiceError(
                    f"ComfyUI 查詢歷史失敗（HTTP {response.status_code}）：{endpoint} "
                    f"{_body_excerpt(response)}"
                )

            entry = _json(response, endpoint).get(prompt_id)
            if entry is not None and _is_finished(entry):
                _abort_on_error_status(entry, prompt_id)
                return entry

            await asyncio.sleep(self._settings.poll_interval)

    def _image_ref(self, entry: dict[str, Any], prompt_id: str) -> dict[str, str]:
        """從 `outputs` 取出輸出節點的第一張圖。"""
        node_id = self._settings.nodes.output_node_id
        outputs = entry.get("outputs") or {}
        node_output = outputs.get(node_id)
        if not node_output:
            raise ExternalServiceError(
                f"ComfyUI 的執行結果中沒有輸出節點 {node_id!r}"
                f"（COMFYUI_OUTPUT_NODE_ID）。prompt_id={prompt_id}，"
                f"實際有輸出的節點：{', '.join(sorted(outputs)) or '（無）'}"
            )
        images = node_output.get("images") or []
        if not images:
            raise ExternalServiceError(
                f"ComfyUI 的輸出節點 {node_id!r} 沒有產出圖片。prompt_id={prompt_id}"
            )
        return images[0]

    async def _download(self, http: httpx.AsyncClient, ref: dict[str, str]) -> bytes:
        """把 `/history` 給的檔名資訊換成圖片本體。"""
        endpoint = f"{self.base_url}/view"
        params = {
            "filename": ref.get("filename", ""),
            "subfolder": ref.get("subfolder", ""),
            "type": ref.get("type", "output"),
        }
        try:
            response = await http.get(endpoint, params=params)
        except httpx.HTTPError as error:
            raise ExternalServiceError(f"ComfyUI 連線失敗：{endpoint}（{error}）") from error

        if response.status_code >= 400:
            raise ExternalServiceError(
                f"ComfyUI 下載圖片失敗（HTTP {response.status_code}）："
                f"{endpoint} filename={params['filename']}"
            )
        if not response.content:
            raise ExternalServiceError(
                f"ComfyUI 回傳空的圖片內容：{endpoint} filename={params['filename']}"
            )
        return response.content


def _is_finished(entry: dict[str, Any]) -> bool:
    """該筆歷史是否已跑完。

    只有 `outputs` 還不夠——執行中的任務也可能已寫入部分輸出。以 `status.completed`
    為準；舊版沒有這個欄位時退而求其次看 `outputs`。
    """
    status = entry.get("status")
    if isinstance(status, dict) and "completed" in status:
        return bool(status["completed"]) or status.get("status_str") == "error"
    return bool(entry.get("outputs"))


def _abort_on_error_status(entry: dict[str, Any], prompt_id: str) -> None:
    """歷史紀錄裡標了錯誤就直接失敗——WS 斷線時這是唯一能看到錯誤的地方。"""
    status = entry.get("status")
    if not isinstance(status, dict) or status.get("status_str") != "error":
        return
    detail = ""
    for name, payload in status.get("messages") or []:
        if name == "execution_error" and isinstance(payload, dict):
            detail = (
                f"節點 {payload.get('node_id')}："
                f"{payload.get('exception_type', '')} {payload.get('exception_message', '')}"
            ).strip()
            break
    raise ExternalServiceError(f"ComfyUI 執行失敗（prompt_id={prompt_id}）{detail}".strip())


def _json(response: httpx.Response, endpoint: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as error:
        raise ExternalServiceError(
            f"ComfyUI 回應不是合法 JSON：{endpoint}（{error}）"
        ) from error
    if not isinstance(data, dict):
        raise ExternalServiceError(
            f"ComfyUI 回應格式非預期（實得 {type(data).__name__}）：{endpoint}"
        )
    return data


def _body_excerpt(response: httpx.Response, limit: int = 300) -> str:
    """把錯誤回應的內容截一段放進訊息——`node_errors` 通常直指哪個節點設錯。"""
    try:
        text = response.text
    except (UnicodeDecodeError, httpx.ResponseNotRead):  # pragma: no cover - 極少見
        return ""
    text = " ".join(text.split())
    return text[:limit] + "…" if len(text) > limit else text
