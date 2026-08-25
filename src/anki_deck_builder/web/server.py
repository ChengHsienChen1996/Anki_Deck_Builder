"""FastAPI 服務（介面層）。

每個端點都是 `service.py` 的薄殼：解析參數 → 呼叫 service → 轉成 JSON。
**這裡不得出現任何 if 判斷狀態、組欄位名、拼檔案路徑的程式碼**，
那些一律在 `service.py` 之下（約束 4，phase-5-webui.md 的頭號風險）。

Gradio 的介面於 Task 5.2 以 `gr.mount_gradio_app()` 掛在同一個 app 上，
兩者共用同一個行程、同一個 `StageRunner`——所以「同時只跑一個階段」對
API 與 UI 一起生效。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from ..config import Settings, load_settings
from ..state import CardStore
from . import service
from .tasks import StageBusyError, StageRunner

#: 正反面 → CSV 的圖片欄位。路徑一律由 `service.media_file()` 依 card_id 反查，
#: 不接受呼叫端傳檔名進來。
#: **只開放圖片**：語音試聽是 phase 文件沒要求的功能，本 phase 不做
IMAGE_FIELDS: dict[str, str] = {"front": "image_front", "back": "image_back"}


def create_app(
    settings: Settings | None = None,
    work: Path | str | None = None,
    with_ui: bool = True,
) -> FastAPI:
    """組出 app。

    Args:
        settings: 設定；`None` 時載入 `.env`。測試注入假設定。
        work: 中間 CSV 路徑；`None` 時取設定的預設值。
        with_ui: 是否掛上 Gradio 介面。端點測試設 `False`——匯入 gradio
            要好幾秒，而那些測試一個元件都用不到。
    """
    settings = settings or load_settings()
    store = CardStore(Path(work) if work else settings.paths.cards_csv)
    runner = StageRunner()

    app = FastAPI(title="anki-deck-builder", version="0.5.0")
    app.state.settings = settings
    app.state.store = store
    app.state.runner = runner

    @app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        return await _guard(service.status_summary(store))

    @app.get("/api/rows")
    async def get_rows(
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 50,
        deck: str | None = None,
        stage: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        page = await _guard(
            service.list_rows(
                store, offset=offset, limit=limit, deck=deck, stage=stage, status=status
            )
        )
        return {"total": page.total, "offset": page.offset, "rows": page.rows}

    @app.patch("/api/rows/{card_id}")
    async def patch_row(
        card_id: str,
        updates: Annotated[dict[str, Any], Body()],
    ) -> dict[str, Any]:
        return await _guard(service.update_row(store, card_id, updates))

    @app.post("/api/stages/{stage}/run")
    async def run_stage(
        stage: str,
        force: bool = False,
        only_failed: bool = False,
        card_ids: Annotated[list[str] | None, Body(embed=True)] = None,
    ) -> dict[str, Any]:
        if stage not in service.RUNNABLE_STAGES:
            raise HTTPException(404, f"未知的階段：{stage}")
        if force and only_failed:
            raise HTTPException(400, "force 與 only_failed 互斥，不可同時指定")

        async def work() -> list[Any]:
            return await service.run_stage(
                settings,
                store,
                stage,
                force=force,
                only_failed=only_failed,
                card_ids=card_ids,
            )

        try:
            state = runner.start(stage, work)
        except StageBusyError as busy:
            raise HTTPException(409, str(busy)) from busy
        return {"stage": state.stage, "accepted": True}

    @app.get("/api/stages/{stage}/progress")
    async def get_progress(stage: str) -> dict[str, Any]:
        if stage not in service.RUNNABLE_STAGES:
            raise HTTPException(404, f"未知的階段：{stage}")
        return await _guard(
            service.stage_progress(store, stage, runner.state_for(stage))
        )

    @app.get("/api/failed")
    async def get_failed() -> dict[str, Any]:
        return {"failed": await _guard(service.failed_list(store))}

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return service.config_view(settings)

    @app.get("/api/media/img/{card_id}")
    async def get_image(
        card_id: str,
        side: Annotated[str, Query(pattern="^(front|back)$")] = "front",
    ) -> FileResponse:
        path = await _guard(service.media_file(store, card_id, IMAGE_FIELDS[side]))
        return FileResponse(path)

    if with_ui:
        app = _mount_ui(app, settings, store, runner)
    return app


def _mount_ui(
    app: FastAPI, settings: Settings, store: CardStore, runner: StageRunner
) -> FastAPI:
    """把 Gradio 掛在根路徑上。

    `/api/*` 的路由在此之前就註冊好了，FastAPI 依序比對，所以掛在 `"/"`
    不會把 API 蓋掉——開瀏覽器直接看到 UI，curl 打 API 照常。

    `ssr_mode=False`：SSR 需要 Node，本專案的前置需求裡沒有它。
    匯入延後到這裡，`--help` 與其他子命令不必付 gradio 的啟動成本。
    """
    import gradio as gr

    from .ui import create_ui

    return gr.mount_gradio_app(
        app, create_ui(settings, store, runner), path="/", ssr_mode=False
    )


async def _guard(awaitable: Any) -> Any:
    """把 `ServiceError` 轉成 404。

    service 只在「找不到／不合法」時拋這個例外，其餘例外照常往上竄成 500——
    那才是真的壞了，不該被包裝成看起來像使用者輸入問題。
    """
    try:
        return await awaitable
    except service.ServiceError as error:
        raise HTTPException(404, str(error)) from error


async def serve(
    host: str = "127.0.0.1",
    port: int = 7860,
    work: Path | str | None = None,
) -> None:
    """啟動服務，直到 Ctrl-C。供 CLI 的 `serve` 子命令 await。

    用 `uvicorn.Server.serve()` 而不是 `uvicorn.run()`：後者自己會開一個事件迴圈，
    而 CLI 的 `main()` 早就在 `asyncio.run()` 裡了，兩個迴圈會直接撞上
    「asyncio.run() cannot be called from a running event loop」——實測踩到過。

    預設綁 `127.0.0.1`：這是本機製卡工具，沒有帳號、沒有權限控管，
    也不該有——把它暴露到區網等於把工作檔開放給任何人改。
    """
    import uvicorn

    config = uvicorn.Config(create_app(work=work), host=host, port=port)
    await uvicorn.Server(config).serve()
