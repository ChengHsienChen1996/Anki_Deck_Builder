"""Gradio 介面（介面層）。

五個分頁，掛在 `server.py` 的 FastAPI app 上（`gr.mount_gradio_app`），
與 API 共用同一個行程與同一個 `StageRunner`——所以「同時只跑一個階段」
對 UI 與 API 一起生效。

**每個 callback 都只呼叫 `service.py`**，自己不碰 `stages/`、`state/`，也不
重複任何狀態判斷。要新增行為時先問「這句 if 屬於介面還是業務」——屬於業務就
往下搬（約束 4）。

## Gradio 6 的破壞性變更

`gr.Dataframe` 的 `col_count` 已停用，改為 `column_count`／`column_limits`；
`row_count` 改成單一整數。本檔一律用新參數，不照 5.x 的記憶寫。
"""

from __future__ import annotations

from typing import Any

import gradio as gr

from ..config import Settings
from ..state import STAGE_NAMES, CardStore
from . import service
from .tasks import StageBusyError, StageRunner

#: 狀態總覽上每顆執行按鈕對應的階段。`audio` 是別名（兩側共用一次模型載入），
#: 因此這裡不列 `audio_front`／`audio_back`——真要單側重跑，失敗清單分頁才是入口
RUN_BUTTONS: tuple[tuple[str, str], ...] = (
    ("ocr", "① OCR"),
    ("extract", "② 抽取"),
    ("image", "③ 聯想圖"),
    (service.AUDIO_BOTH, "④ 語音（兩側）"),
)

#: 進度輪詢間隔（秒）。階段動輒數分鐘，一秒一次已經夠即時，也不會讓
#: 中間 CSV 被重讀到影響寫入
POLL_SECONDS = 1.0

_STATUS_HEADERS = ["階段", "pending", "done", "failed"]


def create_ui(settings: Settings, store: CardStore, runner: StageRunner) -> gr.Blocks:
    """組出五個分頁。

    Args:
        settings: 供「設定」分頁顯示（金鑰由 `service.config_view()` 遮罩）。
        store: 中間 CSV。
        runner: 與 API 共用的背景執行器。
    """
    with gr.Blocks(title="anki-deck-builder", fill_height=True) as blocks:
        gr.Markdown("# anki-deck-builder\n本機製卡流程的檢視與重跑介面。")

        with gr.Tabs():
            with gr.Tab("狀態總覽"):
                _status_tab(blocks, settings, store, runner)
            with gr.Tab("抽取結果"):
                gr.Markdown("_Task 5.3 實作：可編輯表格與狀態連動。_")
            with gr.Tab("聯想圖"):
                gr.Markdown("_Task 5.4 實作：縮圖牆與單張重生。_")
            with gr.Tab("失敗清單"):
                gr.Markdown("_Task 5.5 實作：失敗列集中顯示與批次重跑。_")
            with gr.Tab("設定"):
                _config_tab(settings)

    return blocks


def _status_tab(
    blocks: gr.Blocks, settings: Settings, store: CardStore, runner: StageRunner
) -> None:
    """各階段統計、執行按鈕與進度。"""
    gr.Markdown("### 各階段狀態")
    table = gr.Dataframe(
        headers=_STATUS_HEADERS,
        datatype="str",
        interactive=False,
        column_count=len(_STATUS_HEADERS),
        label="各階段的 pending／done／failed",
    )

    gr.Markdown("### 執行")
    with gr.Row():
        force = gr.Checkbox(label="force：忽略 done，全部重跑")
        only_failed = gr.Checkbox(label="only-failed：只重跑失敗的列")
    gr.Markdown(
        "_兩者互斥。執行中的階段結束前，其他階段的按鈕會被拒絕——"
        "GPU 只有一張，並行只會互相搶 VRAM。_"
    )

    with gr.Row():
        buttons = [(stage, gr.Button(label)) for stage, label in RUN_BUTTONS]

    progress = gr.Markdown()
    timer = gr.Timer(POLL_SECONDS)

    async def refresh() -> list[list[str]]:
        summary = await service.status_summary(store)
        return [
            [stage, str(counts["pending"]), str(counts["done"]), str(counts["failed"])]
            for stage, counts in summary.items()
        ]

    async def start(stage: str, force_on: bool, only_failed_on: bool) -> str:
        if force_on and only_failed_on:
            return "⚠️ force 與 only-failed 互斥，請只勾一個。"
        try:
            runner.start(
                stage,
                lambda: service.run_stage(
                    settings,
                    store,
                    stage,
                    force=force_on,
                    only_failed=only_failed_on,
                ),
            )
        except StageBusyError as busy:
            return f"⚠️ {busy}"
        return f"已開始執行 **{stage}**。"

    async def tick() -> tuple[Any, Any]:
        """輪詢：更新進度文字與統計表。

        沒有任何階段跑過時 `gr.skip()` 不動畫面——每秒重畫一次空白訊息
        會讓游標與捲軸一直跳。
        """
        stage = runner.running_stage or _last_stage(runner)
        if stage is None:
            return gr.skip(), gr.skip()
        report = await service.stage_progress(store, stage, runner.state_for(stage))
        return _format_progress(report), await refresh()

    for stage, button in buttons:
        button.click(
            _bind(start, stage), inputs=[force, only_failed], outputs=progress
        ).then(refresh, outputs=table)

    timer.tick(tick, outputs=[progress, table])
    blocks.load(refresh, outputs=table)


def _config_tab(settings: Settings) -> None:
    """`.env` 生效值，唯讀。金鑰由 service 遮罩，這裡不碰原始值。"""
    gr.Markdown(
        "### 生效設定\n"
        "唯讀。要改請編輯 `.env` 後重新啟動服務——"
        "UI 改設定會與正在跑的階段搶同一份設定物件。"
    )
    gr.JSON(value=service.config_view(settings), label="設定（金鑰已遮罩）")


def _bind(fn: Any, stage: str) -> Any:
    """把階段名綁進 callback；Gradio 只會傳元件值進來。"""

    async def wrapped(force_on: bool, only_failed_on: bool) -> str:
        return await fn(stage, force_on, only_failed_on)

    return wrapped


def _last_stage(runner: StageRunner) -> str | None:
    for stage in (*STAGE_NAMES, service.AUDIO_BOTH):
        if runner.state_for(stage) is not None:
            return stage
    return None


def _format_progress(report: dict[str, Any]) -> str:
    counts = report["counts"]
    head = f"**{report['stage']}**："
    body = (
        f"done {counts['done']}／"
        f"{counts['pending'] + counts['done'] + counts['failed']}"
        f"（failed {counts['failed']}）"
    )
    if report["running"]:
        return f"{head}{body} — 執行中 {report['elapsed']:.0f}s"
    if report["error"]:
        return f"{head}{body} — ❌ {report['error']}"
    if report["results"]:
        done = "、".join(
            f"{r['stage']} 成功 {r['succeeded']}／失敗 {r['failed']}"
            for r in report["results"]
        )
        return f"{head}{body} — 已完成（{done}）"
    return f"{head}{body}"
