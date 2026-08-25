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
import numpy as np

from ..config import Settings
from ..schemas import StageStatus
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

#: 抽取結果表格的欄位。第一欄 `card_id` 是主鍵，設為唯讀——它一改，
#: 儲存時就對不回原本那一列了
EDIT_COLUMNS: tuple[str, ...] = (
    "card_id",
    "deck",
    "front",
    "back",
    "example",
    "image_prompt",
    "tts_front_text",
    "tts_back_text",
    "difficulty",
    "tags",
)

#: 一頁幾列。308 張卡一次全載會讓瀏覽器明顯卡頓
PAGE_SIZE = 25

#: 篩選下拉的「不篩選」選項。Gradio 的 Dropdown 沒有空值概念，用哨兵字串
ANY_OPTION = "（全部）"

#: 縮圖牆一次幾張。308 張圖一次載完是 127 MB，瀏覽器會直接卡住
GALLERY_PAGE = 24

#: 縮圖牆的欄數
GALLERY_COLUMNS = 6

#: 還沒生成時的佔位圖。用陣列而不是檔案——多一個要跟著打包的靜態資產，
#: 只為了畫一塊灰色，不值得
PLACEHOLDER = np.full((144, 256, 3), 60, dtype=np.uint8)


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
                _rows_tab(blocks, store)
            with gr.Tab("聯想圖"):
                _images_tab(blocks, settings, store, runner)
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


def _rows_tab(blocks: gr.Blocks, store: CardStore) -> None:
    """卡片表格：篩選、分頁、逐欄編輯與儲存。

    儲存時**整頁的可編輯欄位都送給 service**，由它決定哪些真的變了、要重置
    哪些階段。UI 自己比對「哪一格被改過」等於把連動規則抄第二份——
    正是本 phase 要避免的事。
    """
    gr.Markdown(
        "### 卡片\n"
        "可直接在表格內編輯，按「儲存變更」寫回。存檔後該列的 `extract_status` "
        "會回到 `pending`；改 `image_prompt` 連帶 `image_status`，改 `tts_*_text` "
        "連帶**對應那一側**的 `audio_*_status`。`card_id` 是主鍵，不可修改。"
    )

    with gr.Row():
        deck = gr.Dropdown(
            choices=[ANY_OPTION], value=ANY_OPTION, label="deck", filterable=True
        )
        stage = gr.Dropdown(
            choices=[ANY_OPTION, *STAGE_NAMES], value=ANY_OPTION, label="階段"
        )
        status = gr.Dropdown(
            choices=[ANY_OPTION, *(s.value for s in StageStatus)],
            value=ANY_OPTION,
            label="狀態（需搭配階段）",
        )
        reload_button = gr.Button("重新載入", scale=0)

    with gr.Row():
        prev_button = gr.Button("← 上一頁", scale=0)
        page_label = gr.Markdown()
        next_button = gr.Button("下一頁 →", scale=0)

    table = gr.Dataframe(
        headers=list(EDIT_COLUMNS),
        datatype="str",
        type="array",
        column_count=len(EDIT_COLUMNS),
        static_columns=[0],
        interactive=True,
        wrap=True,
        max_height=520,
        label=None,
    )
    save_button = gr.Button("儲存變更", variant="primary")
    message = gr.Markdown()

    offset = gr.State(0)
    loaded_ids = gr.State([])

    async def load(
        deck_value: str, stage_value: str, status_value: str, start: int
    ) -> tuple[Any, ...]:
        try:
            page = await service.list_rows(
                store,
                offset=max(0, start),
                limit=PAGE_SIZE,
                deck=_optional(deck_value),
                stage=_optional(stage_value),
                status=_optional(status_value),
            )
        except service.ServiceError as error:
            return gr.skip(), gr.skip(), f"⚠️ {error}", gr.skip(), gr.skip()

        data = [[str(row[name]) for name in EDIT_COLUMNS] for row in page.rows]
        ids = [row["card_id"] for row in page.rows]
        return data, ids, _page_label(page), page.offset, gr.update(
            choices=[ANY_OPTION, *await service.deck_names(store)]
        )

    async def save(data: list[list[str]], ids: list[str]) -> str:
        if not ids:
            return "沒有可儲存的列。"
        edits = {
            card_id: {
                name: value
                for name, value in zip(EDIT_COLUMNS, row, strict=False)
                if name != "card_id"
            }
            for card_id, row in zip(ids, data, strict=False)
        }
        try:
            result = await service.update_rows(store, edits)
        except service.ServiceError as error:
            return f"⚠️ {error}"
        if not result["updated"]:
            return "沒有任何變動。"
        return (
            f"已更新 {len(result['updated'])} 列"
            f"（{'、'.join(result['updated'][:8])}"
            f"{'…' if len(result['updated']) > 8 else ''}），"
            f"重置階段：{'、'.join(result['reset'])}。"
        )

    outputs = [table, loaded_ids, page_label, offset, deck]
    filters = [deck, stage, status]

    reload_button.click(load, inputs=[*filters, offset], outputs=outputs)
    for control in filters:
        # 換篩選條件時回到第一頁——留在第 5 頁會看到空表格，像是壞掉
        control.change(lambda: 0, outputs=offset).then(
            load, inputs=[*filters, offset], outputs=outputs
        )
    prev_button.click(
        lambda start: max(0, start - PAGE_SIZE), inputs=offset, outputs=offset
    ).then(load, inputs=[*filters, offset], outputs=outputs)
    next_button.click(
        lambda start: start + PAGE_SIZE, inputs=offset, outputs=offset
    ).then(load, inputs=[*filters, offset], outputs=outputs)
    save_button.click(save, inputs=[table, loaded_ids], outputs=message).then(
        load, inputs=[*filters, offset], outputs=outputs
    )
    blocks.load(load, inputs=[*filters, offset], outputs=outputs)


def _optional(value: str) -> str | None:
    """把「（全部）」哨兵轉回 `None`。"""
    return None if value == ANY_OPTION else value


def _page_label(page: service.RowPage) -> str:
    if page.total == 0:
        return "沒有符合條件的卡片。"
    first = page.offset + 1
    last = min(page.offset + PAGE_SIZE, page.total)
    return f"第 **{first}–{last}** 列／共 **{page.total}** 張卡"


def _images_tab(
    blocks: gr.Blocks, settings: Settings, store: CardStore, runner: StageRunner
) -> None:
    """縮圖牆、單張預覽與重生。

    重生走的是與 CLI 同一條路：改 prompt → `image_status` 回 `pending` →
    以 `card_ids=[該卡]` 觸發 image 階段（骨架的擴充參數）。
    """
    gr.Markdown(
        "### 聯想圖\n"
        "點縮圖看大圖與 prompt。改完 prompt 按「生成／重生此圖」，"
        "**只會重跑這一張**，其他圖不動。"
    )

    with gr.Row():
        prev_button = gr.Button("← 上一頁", scale=0)
        page_label = gr.Markdown()
        next_button = gr.Button("下一頁 →", scale=0)
        reload_button = gr.Button("重新載入", scale=0)

    gallery = gr.Gallery(
        columns=GALLERY_COLUMNS,
        height=430,
        object_fit="cover",
        allow_preview=False,
        label=None,
    )

    with gr.Row():
        with gr.Column(scale=1):
            preview = gr.Image(label="大圖", height=320)
        with gr.Column(scale=2):
            selected = gr.Markdown("點一張縮圖以檢視。")
            prompt = gr.Textbox(
                label="image_prompt", lines=4, interactive=True, max_lines=8
            )
            regenerate = gr.Button("生成／重生此圖", variant="primary")
            message = gr.Markdown()

    offset = gr.State(0)
    page_ids = gr.State([])
    current = gr.State("")

    async def load(start: int) -> tuple[Any, ...]:
        total, entries = await service.image_gallery(
            store, offset=max(0, start), limit=GALLERY_PAGE
        )
        items = [
            (str(entry.image) if entry.image else PLACEHOLDER, _caption(entry))
            for entry in entries
        ]
        ids = [entry.card_id for entry in entries]
        return items, ids, _gallery_label(max(0, start), total, len(entries)), max(0, start)

    async def select(ids: list[str], event: gr.SelectData) -> tuple[Any, ...]:
        card_id = ids[event.index] if event.index < len(ids) else ""
        if not card_id:
            return gr.skip(), gr.skip(), gr.skip(), gr.skip()
        entry = await _entry(store, card_id)
        return (
            str(entry.image) if entry.image else None,
            _detail(entry),
            entry.prompt,
            card_id,
        )

    async def rerun(card_id: str, new_prompt: str) -> tuple[Any, ...]:
        """存 prompt、重跑這一張，跑完才回傳——單張約數秒，等它比較直觀。"""
        if not card_id:
            return "⚠️ 先點一張縮圖。", gr.skip(), gr.skip()
        try:
            await service.update_row(store, card_id, {"image_prompt": new_prompt})
            runner.start(
                "image",
                lambda: service.run_stage(
                    settings, store, "image", card_ids=[card_id]
                ),
            )
        except (service.ServiceError, StageBusyError) as error:
            return f"⚠️ {error}", gr.skip(), gr.skip()

        await runner.wait()
        state = runner.state_for("image")
        if state is not None and state.error:
            return f"❌ {state.error}", gr.skip(), gr.skip()

        # 連同標題一起更新——只換圖不換狀態文字，畫面會停在重生前的 pending
        entry = await _entry(store, card_id)
        return (
            f"已重生 **{card_id}**。",
            str(entry.image) if entry.image else None,
            _detail(entry),
        )

    outputs = [gallery, page_ids, page_label, offset]
    reload_button.click(load, inputs=offset, outputs=outputs)
    prev_button.click(
        lambda start: max(0, start - GALLERY_PAGE), inputs=offset, outputs=offset
    ).then(load, inputs=offset, outputs=outputs)
    next_button.click(
        lambda start: start + GALLERY_PAGE, inputs=offset, outputs=offset
    ).then(load, inputs=offset, outputs=outputs)
    gallery.select(select, inputs=page_ids, outputs=[preview, selected, prompt, current])
    regenerate.click(
        rerun, inputs=[current, prompt], outputs=[message, preview, selected]
    ).then(load, inputs=offset, outputs=outputs)
    blocks.load(load, inputs=offset, outputs=outputs)


async def _entry(store: CardStore, card_id: str) -> service.GalleryEntry:
    """取單一張卡的縮圖牆資料。"""
    _total, entries = await service.image_gallery(store, limit=0)
    return next(entry for entry in entries if entry.card_id == card_id)


def _detail(entry: service.GalleryEntry) -> str:
    return f"**{entry.card_id}** — {entry.front}（image：`{entry.status}`）"


def _caption(entry: service.GalleryEntry) -> str:
    """縮圖下方的說明：card_id 與 front，沒有圖時標出來。"""
    head = f"{entry.card_id} · {entry.front[:18]}"
    return head if entry.image else f"{head}（未生成）"


def _gallery_label(start: int, total: int, shown: int) -> str:
    if total == 0:
        return "工作檔裡還沒有卡片。"
    return f"第 **{start + 1}–{start + shown}** 張／共 **{total}** 張卡"


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
