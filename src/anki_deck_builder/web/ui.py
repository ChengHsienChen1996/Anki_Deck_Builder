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

#: 失敗清單的欄位
FAILED_HEADERS = ["card_id", "階段", "front", "錯誤訊息"]

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
                status_table, refresh_status = _status_tab(
                    blocks, settings, store, runner
                )
            with gr.Tab("匯入"):
                _import_tab(settings, store, runner, status_table, refresh_status)
            with gr.Tab("抽取結果"):
                _rows_tab(blocks, store)
            with gr.Tab("聯想圖"):
                _images_tab(blocks, settings, store, runner)
            with gr.Tab("失敗清單"):
                _failed_tab(blocks, settings, store, runner)
            with gr.Tab("設定"):
                _config_tab(settings)

    return blocks


def _status_tab(
    blocks: gr.Blocks, settings: Settings, store: CardStore, runner: StageRunner
) -> tuple[gr.Dataframe, Any]:
    """各階段統計、執行按鈕與進度。

    Returns:
        `(統計表元件, 重新整理的 callback)`——匯入分頁在匯入成功後要刷新它，
        否則新加的來源列要等下一次開頁才看得到。
    """
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
    return table, refresh


def _import_tab(
    settings: Settings,
    store: CardStore,
    runner: StageRunner,
    status_table: gr.Dataframe,
    refresh_status: Any,
) -> None:
    """把伺服器上的路徑收進工作檔。

    **不做瀏覽器上傳**：伺服器與瀏覽器是同一台機器，上傳等於把檔案複製一份
    再讀回來。輸入路徑與 CLI 的 `ocr --input` 是同一件事，行為也完全一致。
    """
    gr.Markdown(
        "### 匯入\n"
        "輸入**這台機器上**的路徑：單張圖、內含圖片的資料夾、PDF，或 `.txt`／`.md`。\n"
        "「檢查」只判別會抓到哪些檔案、不寫任何東西；「匯入」才會建立來源列。\n"
        "匯入**不會**跑 OCR——建好列之後到「狀態總覽」按 **① OCR**。"
    )

    with gr.Row():
        path = gr.Textbox(
            label="路徑",
            placeholder="/home/jason/scans/n2_book",
            scale=4,
            autofocus=True,
        )
        check_button = gr.Button("檢查", scale=0)
        import_button = gr.Button("匯入", variant="primary", scale=0)

    message = gr.Markdown()
    listing = gr.Dataframe(
        headers=["將處理的檔案（依順序）"],
        datatype="str",
        type="array",
        column_count=1,
        interactive=False,
        max_height=280,
        label=None,
    )

    async def check(target: str) -> tuple[Any, Any]:
        if not target.strip():
            return "⚠️ 先輸入路徑。", gr.skip()
        try:
            result = await service.preview_input(target.strip())
        except service.ServiceError as error:
            return f"⚠️ {error}", []
        note = f"判別為 **{result['kind']}**，共 **{result['total']}** 個項目。"
        if result["truncated"]:
            note += f"（以下只列前 {service.PREVIEW_LIMIT} 個）"
        return note, [[name] for name in result["items"]]

    async def do_import(target: str) -> tuple[Any, Any]:
        if not target.strip():
            return "⚠️ 先輸入路徑。", gr.skip()
        try:
            result = await service.ingest_path(
                settings, store, target.strip(), runner=runner
            )
        except (service.ServiceError, StageBusyError) as error:
            return f"⚠️ {error}", gr.skip()
        note = (
            f"已匯入 **{result['created']}** 列"
            + (f"（略過 {result['skipped']} 個已在工作檔中的來源）" if result["skipped"] else "")
            + "。接著到「狀態總覽」按 **① OCR**。"
        )
        return note, gr.skip()

    check_button.click(check, inputs=path, outputs=[message, listing])
    import_button.click(do_import, inputs=path, outputs=[message, listing]).then(
        refresh_status, outputs=status_table
    )
    path.submit(check, inputs=path, outputs=[message, listing])


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


def _failed_tab(
    blocks: gr.Blocks, settings: Settings, store: CardStore, runner: StageRunner
) -> None:
    """所有階段的失敗列，單列重跑與批次重跑。

    一列可能同時在多個階段失敗，那就會出現多筆——它們是各自獨立的失敗
    （`audio_front` 壞了不代表 `audio_back` 也壞），要各自重跑。
    """
    gr.Markdown(
        "### 失敗清單\n"
        "點一列看完整錯誤訊息。「重跑此列」只跑那一張卡的那一個階段，"
        "跑完才回來；「重跑全部失敗」等同 CLI 的 `--only-failed`，"
        "**在背景跑**，進度看「狀態總覽」。"
    )

    with gr.Row():
        stage_filter = gr.Dropdown(
            choices=[ANY_OPTION, *STAGE_NAMES], value=ANY_OPTION, label="階段"
        )
        reload_button = gr.Button("重新載入", scale=0)

    count_note = gr.Markdown()
    table = gr.Dataframe(
        headers=FAILED_HEADERS,
        datatype="str",
        type="array",
        column_count=len(FAILED_HEADERS),
        interactive=False,
        wrap=True,
        max_height=320,
        label=None,
    )
    detail = gr.Textbox(label="完整錯誤訊息", lines=4, max_lines=12, interactive=False)

    with gr.Row():
        rerun_one = gr.Button("重跑此列", variant="primary")
        rerun_all = gr.Button("重跑全部失敗")
    # 動作結果與筆數分開兩個元件：共用一個的話，重跑後的 reload 會立刻
    # 把「已重跑 X」洗掉，使用者只看得到筆數變了
    message = gr.Markdown()

    rows_state = gr.State([])
    picked = gr.State(None)

    async def load(stage_value: str) -> tuple[Any, ...]:
        failures = await service.failed_list(store, _optional(stage_value))
        data = [
            [f["card_id"], f["stage"], f["front"][:24], _one_line(f["error"])]
            for f in failures
        ]
        note = "目前沒有失敗的列。" if not failures else f"共 **{len(failures)}** 筆失敗。"
        return data, failures, note, "", None

    async def select(failures: list[dict[str, Any]], event: gr.SelectData) -> tuple[Any, ...]:
        index = event.index[0] if isinstance(event.index, list) else event.index
        if index is None or index >= len(failures):
            return gr.skip(), gr.skip()
        failure = failures[index]
        return failure["error"], failure

    async def rerun_selected(failure: dict[str, Any] | None) -> str:
        if not failure:
            return "⚠️ 先點一列。"
        try:
            runner.start(
                failure["stage"],
                lambda: service.run_stage(
                    settings,
                    store,
                    failure["stage"],
                    card_ids=[failure["card_id"]],
                ),
            )
        except StageBusyError as error:
            return f"⚠️ {error}"

        await runner.wait()
        state = runner.state_for(failure["stage"])
        if state is not None and state.error:
            return f"❌ {state.error}"
        return f"已重跑 **{failure['card_id']}** 的 {failure['stage']}。"

    async def rerun_everything(stage_value: str) -> str:
        """批次重跑不等它——重跑 300 張圖要半小時，卡在這個 callback 上沒有意義。"""
        stage = _optional(stage_value)
        stages = [stage] if stage else await service.failed_stages(store)
        if not stages:
            return "沒有失敗的列可重跑。"
        try:
            runner.start(
                stages[0] if len(stages) == 1 else service.ALL_FAILED,
                lambda: service.run_failed(settings, store, stage),
            )
        except StageBusyError as error:
            return f"⚠️ {error}"
        return f"已在背景重跑：{'、'.join(stages)}。進度看「狀態總覽」。"

    outputs = [table, rows_state, count_note, detail, picked]
    reload_button.click(load, inputs=stage_filter, outputs=outputs)
    stage_filter.change(load, inputs=stage_filter, outputs=outputs)
    table.select(select, inputs=rows_state, outputs=[detail, picked])
    rerun_one.click(rerun_selected, inputs=picked, outputs=message).then(
        load, inputs=stage_filter, outputs=outputs
    )
    rerun_all.click(rerun_everything, inputs=stage_filter, outputs=message)
    blocks.load(load, inputs=stage_filter, outputs=outputs)


def _one_line(error: str) -> str:
    """表格裡先給一行摘要，完整內容點選後看。"""
    single = error.replace("\\n", " ").strip()
    return single if len(single) <= 80 else single[:79] + "…"


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
    for stage in service.PROGRESS_NAMES:
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
