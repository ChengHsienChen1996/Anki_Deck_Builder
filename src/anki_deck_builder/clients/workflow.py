"""ComfyUI workflow 的載入、驗證與節點注入（服務層）。

workflow 由使用者自帶，節點結構因人而異，因此本模組**對 workflow 的內容一無所知**：
要注入哪個節點、哪個欄位，全部由 `ComfyUINodeSettings`（即 `.env`）決定（約束 5）。
模組內不出現任何字面量節點 ID 或 `class_type`。

## 為什麼驗證要獨立成一個函式

生成一批圖可能跑半小時。若「節點 ID 打錯」這種設定問題要等第一次注入才發現，
使用者已經等了幾十秒才看到錯誤；更糟的是分批送出時，錯誤會夾在進度條中間。
`validate()` 由 `ImageStage.validate_settings()` 在**階段開始前**呼叫一次，
把設定問題全部擋在生成之前。

## 注入操作副本

`inject()` 回傳深複製後的新 dict，原 workflow 物件不被修改——同一份 workflow
要重複注入上百張卡的 prompt，就地修改會讓前一張的內容殘留到下一張。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import ComfyUINodeSettings
from ..exceptions import ConfigurationError

#: API 格式的 workflow 以節點 ID 為頂層 key；編輯器格式則是這兩個 key
_EDITOR_FORMAT_KEYS = ("nodes", "links")


@dataclass(frozen=True)
class InjectionPoint:
    """一個注入點：要動哪個節點的哪個欄位，以及它由哪些環境變數指定。

    `field` 為空字串代表「只要求節點存在、不注入」——輸出節點屬於這種：
    生成完成後要從 `/history` 的 `outputs` 取它的圖，但不需要改它的輸入。
    """

    label: str
    node_id: str
    field: str
    id_env: str
    field_env: str
    required: bool


def load_workflow(path: str | Path) -> dict[str, Any]:
    """讀取 API 格式的 workflow JSON。

    Args:
        path: `COMFYUI_WORKFLOW_PATH` 指向的檔案。

    Returns:
        以節點 ID 為 key 的 workflow dict。

    Raises:
        ConfigurationError: 檔案不存在、JSON 無法解析，或格式不是 API 格式。
    """
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(
            f"讀不到 workflow 檔案：{file_path}（{error}）。"
            "請確認 COMFYUI_WORKFLOW_PATH 指向的路徑正確。"
        ) from error

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"workflow 不是合法的 JSON：{file_path}（{error}）") from error

    if not isinstance(data, dict):
        raise ConfigurationError(
            f"workflow 應為 JSON 物件，實得 {type(data).__name__}：{file_path}"
        )

    if all(key in data for key in _EDITOR_FORMAT_KEYS):
        raise ConfigurationError(
            f"{file_path} 是 ComfyUI 的**編輯器格式**，POST /prompt 只接受 API 格式。"
            "請在 ComfyUI 以 Workflow → Export (API)（舊版為 Save (API Format)）重新匯出——"
            "正確的檔案頂層 key 會是節點 ID（\"3\"、\"6\"…），而非 nodes／links。"
        )

    return data


def injection_points(nodes: ComfyUINodeSettings) -> list[InjectionPoint]:
    """把 `.env` 的節點設定攤成注入點清單。

    正向、負向、輸出為必填；seed 與 latent 尺寸選填，節點 ID 留空即代表
    「這份 workflow 不開放這個注入點」，跳過即可。
    """
    return [
        InjectionPoint(
            label="正向 prompt",
            node_id=nodes.positive_node_id,
            field=nodes.positive_field,
            id_env="COMFYUI_POSITIVE_NODE_ID",
            field_env="COMFYUI_POSITIVE_FIELD",
            required=True,
        ),
        InjectionPoint(
            label="負向 prompt",
            node_id=nodes.negative_node_id,
            field=nodes.negative_field,
            id_env="COMFYUI_NEGATIVE_NODE_ID",
            field_env="COMFYUI_NEGATIVE_FIELD",
            required=True,
        ),
        InjectionPoint(
            label="輸出節點",
            node_id=nodes.output_node_id,
            field="",
            id_env="COMFYUI_OUTPUT_NODE_ID",
            field_env="",
            required=True,
        ),
        InjectionPoint(
            label="seed",
            node_id=nodes.seed_node_id,
            field=nodes.seed_field,
            id_env="COMFYUI_SEED_NODE_ID",
            field_env="COMFYUI_SEED_FIELD",
            required=False,
        ),
        InjectionPoint(
            label="latent 寬度",
            node_id=nodes.latent_node_id,
            field=nodes.width_field,
            id_env="COMFYUI_LATENT_NODE_ID",
            field_env="COMFYUI_WIDTH_FIELD",
            required=False,
        ),
        InjectionPoint(
            label="latent 高度",
            node_id=nodes.latent_node_id,
            field=nodes.height_field,
            id_env="COMFYUI_LATENT_NODE_ID",
            field_env="COMFYUI_HEIGHT_FIELD",
            required=False,
        ),
    ]


def validate(workflow: dict[str, Any], nodes: ComfyUINodeSettings) -> None:
    """確認所有已設定的注入點都能在 workflow 中找到。

    由 `ImageStage.validate_settings()` 在階段開始前呼叫，避免設定錯誤拖到
    生成中途才浮現。選填注入點未設定時跳過；一旦設定了就照必填的標準檢查——
    設了卻對不上，是打錯字，不是「不想用」。

    Raises:
        ConfigurationError: 必填注入點未設定，或節點 ID／欄位名在 workflow 中不存在。
            訊息指名是哪個注入點、哪個環境變數、哪個節點 ID。
    """
    for point in injection_points(nodes):
        if not point.node_id:
            if point.required:
                raise ConfigurationError(
                    f"注入點「{point.label}」未設定：請在 .env 填入 {point.id_env}。"
                    "節點 ID 可從 API 格式 workflow 的頂層 key 取得。"
                )
            continue
        _validate_point(workflow, point)


def _validate_point(workflow: dict[str, Any], point: InjectionPoint) -> None:
    node = workflow.get(point.node_id)
    if node is None:
        raise ConfigurationError(
            f"注入點「{point.label}」（{point.id_env}={point.node_id}）"
            f"在 workflow 中找不到節點 {point.node_id!r}。"
            f"現有節點：{_known_nodes(workflow)}"
        )
    if not isinstance(node, dict):
        raise ConfigurationError(
            f"注入點「{point.label}」的節點 {point.node_id!r} 不是物件"
            f"（實得 {type(node).__name__}），workflow 可能不是 API 格式"
        )
    if not point.field:
        return

    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise ConfigurationError(
            f"注入點「{point.label}」的節點 {point.node_id!r} 沒有 inputs 物件，"
            "workflow 可能不是 API 格式"
        )
    if point.field not in inputs:
        raise ConfigurationError(
            f"注入點「{point.label}」（{point.field_env}={point.field}）"
            f"在節點 {point.node_id!r} 中找不到欄位 {point.field!r}。"
            f"該節點可用欄位：{', '.join(sorted(inputs)) or '（無）'}"
        )


def inject(
    workflow: dict[str, Any],
    nodes: ComfyUINodeSettings,
    positive: str,
    negative: str,
    seed: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """把這一張卡的 prompt 與參數注入 workflow，回傳注入後的**副本**。

    Args:
        workflow: `load_workflow()` 的結果。本函式不修改它。
        nodes: 注入點設定（來自 `.env`）。
        positive: 該列的 `image_prompt`。
        negative: 整批一致的負向 prompt。
        seed: 亂數種子；`None` 或未設定 seed 節點時不注入，交由 workflow 自己決定。
        width: 圖片寬度；`None` 或未設定 latent 節點時不注入。
        height: 圖片高度；同上。

    Returns:
        可直接送往 `POST /prompt` 的 workflow 副本。

    Raises:
        ConfigurationError: 注入點對不上 workflow（同 `validate()` 的判準）。
    """
    validate(workflow, nodes)
    result = copy.deepcopy(workflow)

    values: dict[str, Any] = {
        "正向 prompt": positive,
        "負向 prompt": negative,
        "seed": seed,
        "latent 寬度": width,
        "latent 高度": height,
    }
    for point in injection_points(nodes):
        value = values.get(point.label)
        if not point.node_id or not point.field or value is None:
            continue
        result[point.node_id]["inputs"][point.field] = value

    return result


def _known_nodes(workflow: dict[str, Any], limit: int = 12) -> str:
    """列出 workflow 現有的節點 ID，讓「找不到節點」的訊息能直接對照。"""
    keys = sorted(workflow, key=lambda k: (len(k), k))
    shown = ", ".join(keys[:limit])
    return f"{shown}…" if len(keys) > limit else shown or "（無）"
