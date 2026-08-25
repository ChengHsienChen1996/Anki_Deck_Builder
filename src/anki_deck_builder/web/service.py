"""Web UI 的唯一膠水層（介面層）。

FastAPI 的端點（`server.py`）與 Gradio 的 callback（`ui.py`）**都只呼叫這裡**，
自己不碰 `stages/`、不碰 `state/`。這樣「Web 沒有重複實作業務邏輯」是結構上的
保證，而不是靠每次寫程式時自律（約束 4）。

本模組自己也不實作業務邏輯：
- 篩選、狀態流轉 → `state/selector.py`
- 階段怎麼組、VRAM 怎麼讓 → `stages/factory.py`、`stages/vram.py`
- 金鑰遮罩 → `config.mask_secret()`

它只做三件介面層該做的事：把參數轉成階段呼叫、把結果轉成純資料結構、
把「哪個欄位改了要重置哪些階段」這張表放在**一處**。
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings, mask_secret
from ..exceptions import AnkiBuilderError
from ..schemas import CardRow, StageStatus
from ..stages.factory import (
    build_audio_stages,
    build_extract_stage,
    build_image_stage,
    build_ocr_stage,
)
from ..stages.vram import (
    extract_agent_name,
    free_vram_for,
    free_vram_for_local_gpu,
    release_comfyui,
)
from ..state import STAGE_NAMES, CardStore, get_status, summarize
from ..state.selector import stage_fields

logger = logging.getLogger(__name__)

#: `audio` 不是 `STAGE_NAMES` 的成員（狀態層把它拆成兩個獨立階段），但 UI 上
#: 「生成語音」是一個按鈕。這個別名讓兩側共用一次模型載入（77 秒）與一次 VRAM 讓渡
AUDIO_BOTH = "audio"

#: 可觸發的階段名稱
RUNNABLE_STAGES: tuple[str, ...] = (*STAGE_NAMES, AUDIO_BOTH)

#: 編輯欄位 → 除了 `extract` 之外還要一併重置的階段。
#: **這張表只有這一份**：UI 不得再寫一次 if 判斷（phase-5-webui.md 的頭號風險）
FIELD_CASCADES: dict[str, tuple[str, ...]] = {
    "image_prompt": ("image",),
    "tts_front_text": ("audio_front",),
    "tts_back_text": ("audio_back",),
}

#: 內容一改就必須重跑抽取的欄位以外，這些欄位改了也視為抽取結果被人工修正
_ALWAYS_RESET = ("extract",)

#: 不接受從 Web 直接改的欄位：狀態與錯誤由階段骨架維護，媒體路徑由階段填寫
_READONLY_FIELDS = frozenset(
    {
        *(
            field
            for stage in STAGE_NAMES
            for field in (stage_fields(stage).status, stage_fields(stage).error)
        ),
        "image_front",
        "image_back",
        "audio_front",
        "audio_back",
    }
)

#: 可編輯欄位：CardRow 的全部欄位扣掉唯讀那些
EDITABLE_FIELDS: frozenset[str] = frozenset(CardRow.model_fields) - _READONLY_FIELDS


class ServiceError(AnkiBuilderError):
    """請求本身有問題（找不到卡片、欄位不可編輯、階段名稱錯誤）。"""


@dataclass(frozen=True)
class RowPage:
    """一頁卡片列。"""

    total: int
    offset: int
    rows: list[dict[str, Any]]


# ── 讀取 ─────────────────────────────────────────────────────────


async def status_summary(store: CardStore) -> dict[str, dict[str, int]]:
    """各階段的狀態統計，等同 CLI 的 `status`。"""
    rows = await _read(store)
    return {
        stage: {status.value: count for status, count in counts.items()}
        for stage, counts in summarize(rows).items()
    }


async def list_rows(
    store: CardStore,
    offset: int = 0,
    limit: int = 50,
    deck: str | None = None,
    stage: str | None = None,
    status: str | None = None,
) -> RowPage:
    """分頁列出卡片列。

    `stage` + `status` 一起給才會依狀態篩選——「哪一欄是哪一階段的狀態」由
    `state/selector.py` 的 `get_status()` 回答，這裡不自己組欄位名。
    """
    rows = [row for row in await _read(store) if row.card_id]
    if deck:
        rows = [row for row in rows if row.deck == deck]
    if status:
        if stage is None:
            raise ServiceError("依狀態篩選時必須同時指定 stage")
        wanted = _parse_status(status)
        rows = [row for row in rows if get_status(row, _known_stage(stage)) is wanted]

    window = rows[offset : offset + limit] if limit else rows[offset:]
    return RowPage(total=len(rows), offset=offset, rows=[_to_dict(row) for row in window])


async def failed_list(store: CardStore) -> list[dict[str, Any]]:
    """所有階段的失敗列與原因，供「失敗清單」分頁。"""
    rows = await _read(store)
    failures: list[dict[str, Any]] = []
    for stage in STAGE_NAMES:
        fields = stage_fields(stage)
        for row in rows:
            if getattr(row, fields.status) is not StageStatus.FAILED:
                continue
            failures.append(
                {
                    "card_id": row.card_id,
                    "stage": stage,
                    "error": getattr(row, fields.error),
                    "front": row.front or row.raw_text[:40],
                }
            )
    return failures


async def deck_names(store: CardStore) -> list[str]:
    """出現過的 deck，供篩選下拉選單。空的 deck 不列。"""
    seen = {row.deck for row in await _read(store) if row.card_id and row.deck}
    return sorted(seen)


def config_view(settings: Settings) -> dict[str, Any]:
    """`.env` 生效值，**金鑰一律遮罩**。

    遮罩用 `config.mask_secret()`（Phase 1 已實作），不在這裡另寫一份。
    """
    agent = settings.agent_factory
    return {
        "paths": {
            "work_dir": str(settings.paths.work_dir),
            "output_dir": str(settings.paths.output_dir),
            "cards_csv": str(settings.paths.cards_csv),
        },
        "agent_factory": {
            "yaml_settings_file": str(agent.yaml_settings_file),
            "openai_api_key": mask_secret(agent.openai_api_key),
        },
        "ingest": {"mode": settings.ingest.mode},
        "comfyui": {
            "base_url": settings.comfyui.base_url,
            "workflow_path": str(settings.comfyui.workflow_path),
            "free_before_llm": settings.comfyui.free_before_llm,
        },
        "tts": {
            "model_path": str(settings.tts.model_path or ""),
            "voice_description": settings.tts.voice_description,
            "speak_translation": settings.tts.speak_translation,
            "loudness_target_dbfs": settings.tts.loudness_target_dbfs,
        },
        "model_unload": {
            "enabled": settings.model_unload.enabled,
            "before_stage": settings.model_unload.before_stage,
        },
    }


async def media_file(store: CardStore, card_id: str, field: str = "image_front") -> Path:
    """取得某張卡登記的媒體檔絕對路徑。

    只認「該 card_id 在中間 CSV 上登記的欄位值」，**不接受呼叫端傳路徑進來**；
    解析後還必須落在媒體根目錄底下才回傳。CSV 是使用者可編輯的資料，
    沒有這兩道就等於把整個檔案系統開給瀏覽器。

    Raises:
        ServiceError: 卡片不存在、該欄位是空的、檔案不存在，或路徑跳出媒體根目錄。
    """
    if field not in {"image_front", "image_back", "audio_front", "audio_back"}:
        raise ServiceError(f"不是媒體欄位：{field}")

    row = await _find_row(store, card_id)
    value = getattr(row, field)
    if not value:
        raise ServiceError(f"{card_id} 的 {field} 是空的")

    path = _media_path(store, value)
    if path is None:
        raise ServiceError(f"{card_id} 的 {field} 指向的檔案不可用：{value}")
    return path


def _media_path(store: CardStore, value: str) -> Path | None:
    """把 CSV 上的相對路徑解析成絕對路徑；跳出媒體根目錄或檔案不存在則回 `None`。

    CSV 是使用者可編輯的資料，`../` 不能變成讀取任意檔案的管道。
    """
    if not value:
        return None
    root = Path(store.path).parent.resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


@dataclass(frozen=True)
class GalleryEntry:
    """縮圖牆上的一張卡。`image` 為 `None` 代表還沒生成（或檔案不見了）。"""

    card_id: str
    front: str
    prompt: str
    status: str
    image: Path | None


async def image_gallery(
    store: CardStore, offset: int = 0, limit: int = 60
) -> tuple[int, list[GalleryEntry]]:
    """聯想圖分頁的資料：`(總數, 這一頁的項目)`。

    未生成的卡片也一起回傳（`image=None`），UI 才有地方放「生成」按鈕——
    只列已生成的圖，缺圖的卡就再也點不到了。
    """
    rows = [row for row in await _read(store) if row.card_id]
    window = rows[offset : offset + limit] if limit else rows[offset:]
    return len(rows), [
        GalleryEntry(
            card_id=row.card_id,
            front=row.front,
            prompt=row.image_prompt,
            status=row.image_status.value,
            image=_media_path(store, row.image_front),
        )
        for row in window
    ]


# ── 編輯 ─────────────────────────────────────────────────────────


async def update_row(
    store: CardStore, card_id: str, updates: dict[str, Any]
) -> dict[str, Any]:
    """更新單列欄位，並把受影響的階段狀態設回 `pending`。

    連動規則（phase-5-webui.md Task 5.3）：
    - 只要動了內容欄位 → `extract_status` 回 `pending`（人工修正過抽取結果）
    - 動 `image_prompt` → 連帶 `image_status`
    - 動 `tts_front_text`／`tts_back_text` → 連帶**對應那一側**的 `audio_*_status`，
      另一側不動

    Raises:
        ServiceError: 卡片不存在，或想改的是唯讀欄位。
    """
    if not updates:
        raise ServiceError("沒有要更新的欄位")
    illegal = sorted(set(updates) - EDITABLE_FIELDS)
    if illegal:
        raise ServiceError(f"這些欄位不可從 Web 編輯：{'、'.join(illegal)}")

    rows = await _read(store)
    row = _pick(rows, card_id)
    _apply(row, updates)
    await store.write(rows)
    return _to_dict(row)


async def update_rows(
    store: CardStore, edits: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    """一次更新多列，**只讀寫中間 CSV 一次**。

    表格編輯一次可能動到十幾列，逐列 `update_row()` 就是十幾次全檔重寫——
    308 列的工作檔每次都要重新序列化，而且中途失敗會留下半套狀態。

    Returns:
        `{"updated": [card_id...], "reset": [階段名...]}`，供 UI 回報。
        沒有實際變動的列不會出現在 `updated` 裡。

    Raises:
        ServiceError: 有卡片找不到，或想改唯讀欄位。**一列不合法就整批不寫**。
    """
    illegal = sorted({name for fields in edits.values() for name in fields} - EDITABLE_FIELDS)
    if illegal:
        raise ServiceError(f"這些欄位不可從 Web 編輯：{'、'.join(illegal)}")

    rows = await _read(store)
    targets = [(_pick(rows, card_id), updates) for card_id, updates in edits.items()]

    updated: list[str] = []
    reset: list[str] = []
    for row, updates in targets:
        stages = _apply(row, updates)
        if stages:
            updated.append(row.card_id)
            reset.extend(stage for stage in stages if stage not in reset)

    if updated:
        await store.write(rows)
    return {"updated": updated, "reset": reset}


def _apply(row: CardRow, updates: dict[str, Any]) -> list[str]:
    """就地套用欄位並重置受影響的階段，回傳被重置的階段名。"""
    changed = [name for name, value in updates.items() if getattr(row, name) != value]
    for name, value in updates.items():
        setattr(row, name, value)

    stages = _stages_to_reset(changed)
    for stage in stages:
        fields = stage_fields(stage)
        setattr(row, fields.status, StageStatus.PENDING)
        setattr(row, fields.error, "")
    return stages


def _stages_to_reset(changed_fields: Iterable[str]) -> list[str]:
    """哪些階段要因為這些欄位的改動而重置。

    只有**中間欄位與引擎內容欄位**才觸發重置；沒有實際變動則什麼都不重置。
    """
    stages: list[str] = []
    for name in changed_fields:
        for stage in _ALWAYS_RESET:
            if stage not in stages:
                stages.append(stage)
        for stage in FIELD_CASCADES.get(name, ()):
            if stage not in stages:
                stages.append(stage)
    return stages


# ── 執行 ─────────────────────────────────────────────────────────


async def run_stage(
    settings: Settings,
    store: CardStore,
    stage: str,
    force: bool = False,
    only_failed: bool = False,
    card_ids: Collection[str] | None = None,
) -> list[Any]:
    """執行一個階段，回傳 `StageResult` 清單（audio 兩側會有兩筆）。

    階段組裝與 VRAM 讓渡都走與 CLI 同一組函式（`stages/factory.py`、
    `stages/vram.py`），因此 UI 與 CLI 的行為一致——這是 phase 文件驗收第 7 步
    要驗的東西。

    Raises:
        ServiceError: 階段名稱不合法。
    """
    if stage not in RUNNABLE_STAGES:
        raise ServiceError(
            f"未知的階段 {stage!r}，可用：{'、'.join(RUNNABLE_STAGES)}"
        )

    stages = await _prepare(settings, stage)
    results = []
    for one in stages:
        results.append(
            await one.run(
                store, force=force, only_failed=only_failed, card_ids=card_ids
            )
        )
    return results


async def _prepare(settings: Settings, stage: str) -> list[Any]:
    """組出階段並完成該階段的 VRAM 讓渡。順序與 `cli.py` 完全相同。"""
    if stage == "ocr":
        built = build_ocr_stage(settings)
        if not built.is_vision_direct:
            await free_vram_for(settings, built.client.model_endpoint(), notify=logger.info)
        return [built]

    if stage == "extract":
        built = build_extract_stage(settings)
        await release_comfyui(settings, notify=logger.info)
        await free_vram_for(
            settings,
            built.client.model_endpoint(extract_agent_name(settings)),
            notify=logger.info,
        )
        return [built]

    if stage == "image":
        built = build_image_stage(settings)
        await _free_local_gpu(settings)
        return [built]

    side = {"audio_front": "front", "audio_back": "back", AUDIO_BOTH: "both"}[stage]
    built_stages = build_audio_stages(settings, side)
    await _free_local_gpu(settings)
    return built_stages


async def _free_local_gpu(settings: Settings) -> None:
    await free_vram_for_local_gpu(
        settings, notify=logger.info, on_skip=logger.warning
    )


async def stage_progress(
    store: CardStore, stage: str, task_state: Any | None
) -> dict[str, Any]:
    """組出進度回應：任務狀態 + 由中間 CSV 反推的完成數。

    進度數字不另外維護一份計數器——`image`／`audio` 的 `checkpoint_every` 是 1，
    每完成一列就落地，重讀 CSV 得到的數字就是真實進度（執行計畫 §1.3）。
    `ocr`／`extract` 的 checkpoint 是 10，數字以 10 列為粒度跳動。
    """
    return {
        "stage": stage,
        "running": bool(task_state and task_state.running),
        "elapsed": round(task_state.elapsed, 1) if task_state else None,
        "error": task_state.error if task_state else None,
        "results": [_result_dict(r) for r in (task_state.results if task_state else [])],
        "counts": await _counts_for(store, stage),
    }


async def _counts_for(store: CardStore, stage: str) -> dict[str, int]:
    """該階段的狀態統計。`audio` 別名把兩側加總——UI 上它是一顆按鈕。"""
    summary = await status_summary(store)
    names = ("audio_front", "audio_back") if stage == AUDIO_BOTH else (stage,)
    totals = dict.fromkeys(("pending", "done", "failed"), 0)
    for name in names:
        for status, count in summary[name].items():
            totals[status] += count
    return totals


def _result_dict(result: Any) -> dict[str, Any]:
    return {
        "stage": result.stage,
        "processed": result.processed,
        "succeeded": result.succeeded,
        "failed": result.failed,
        "added": result.added,
    }


# ── 共用小工具 ────────────────────────────────────────────────────


async def _read(store: CardStore) -> list[CardRow]:
    """讀中間 CSV；檔案還不存在時視為空的，讓 UI 能在空專案上開起來。"""
    return await store.read() if store.exists() else []


async def _find_row(store: CardStore, card_id: str) -> CardRow:
    return _pick(await _read(store), card_id)


def _pick(rows: list[CardRow], card_id: str) -> CardRow:
    for row in rows:
        if row.card_id == card_id:
            return row
    raise ServiceError(f"找不到卡片：{card_id}")


def _known_stage(stage: str) -> str:
    if stage not in STAGE_NAMES:
        raise ServiceError(f"未知的階段 {stage!r}，可用：{'、'.join(STAGE_NAMES)}")
    return stage


def _parse_status(value: str) -> StageStatus:
    try:
        return StageStatus(value)
    except ValueError:
        raise ServiceError(
            f"未知的狀態 {value!r}，可用：{'、'.join(s.value for s in StageStatus)}"
        ) from None


def _to_dict(row: CardRow) -> dict[str, Any]:
    """列轉成純資料。狀態列舉轉字串，JSON 與 Gradio 表格都吃得下。"""
    data = row.model_dump()
    for key, value in data.items():
        if isinstance(value, StageStatus):
            data[key] = value.value
    return data


__all__ = [
    "EDITABLE_FIELDS",
    "FIELD_CASCADES",
    "RUNNABLE_STAGES",
    "GalleryEntry",
    "RowPage",
    "ServiceError",
    "config_view",
    "deck_names",
    "failed_list",
    "image_gallery",
    "list_rows",
    "media_file",
    "run_stage",
    "stage_progress",
    "status_summary",
    "update_row",
    "update_rows",
]
