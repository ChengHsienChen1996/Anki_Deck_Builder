"""workflow 載入、驗證與節點注入的單元測試。

不依賴任何真實 workflow：所有測試用的節點結構都在這裡就地組出來，
確保本模組真的對 workflow 內容一無所知（約束 5）。
"""

from __future__ import annotations

import json

import pytest

from anki_deck_builder.clients.workflow import inject, load_workflow, validate
from anki_deck_builder.config import ComfyUINodeSettings
from anki_deck_builder.exceptions import ConfigurationError


def make_nodes(**overrides: str) -> ComfyUINodeSettings:
    """組出注入點設定。

    每個欄位都明給，避免 `.env` 的實際值滲進測試——`ComfyUINodeSettings`
    的 `model_config` 會讀專案根目錄的 `.env`。
    """
    values = {
        "positive_node_id": "6",
        "positive_field": "text",
        "negative_node_id": "7",
        "negative_field": "text",
        "seed_node_id": "3",
        "seed_field": "seed",
        "latent_node_id": "5",
        "width_field": "width",
        "height_field": "height",
        "output_node_id": "9",
    }
    values.update(overrides)
    return ComfyUINodeSettings(**values)


@pytest.fixture
def workflow() -> dict:
    """一份最小的 API 格式 workflow，節點 ID 刻意與常見範本不同號。"""
    return {
        "3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "原本的正向"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "原本的負向"}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI"}},
    }


# ── load_workflow ────────────────────────────────────────────────


def test_load_workflow_reads_api_format(tmp_path, workflow):
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(workflow), encoding="utf-8")

    assert load_workflow(path) == workflow


def test_load_workflow_missing_file(tmp_path):
    with pytest.raises(ConfigurationError, match="讀不到 workflow 檔案"):
        load_workflow(tmp_path / "不存在.json")


def test_load_workflow_broken_json(tmp_path):
    path = tmp_path / "wf.json"
    path.write_text("{ 不是 JSON", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="不是合法的 JSON"):
        load_workflow(path)


def test_load_workflow_rejects_editor_format(tmp_path):
    """編輯器格式要指名道姓地講，否則使用者只會看到「找不到節點」而不知為何。"""
    path = tmp_path / "wf.json"
    path.write_text(json.dumps({"nodes": [], "links": [], "id": "x"}), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="編輯器格式"):
        load_workflow(path)


def test_load_workflow_rejects_non_object(tmp_path):
    path = tmp_path / "wf.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="應為 JSON 物件"):
        load_workflow(path)


# ── validate ─────────────────────────────────────────────────────


def test_validate_passes_when_all_points_present(workflow):
    validate(workflow, make_nodes())


def test_validate_skips_unset_optional_points(workflow):
    """選填注入點留空代表「這份 workflow 不開放它」，不是錯誤。"""
    validate(workflow, make_nodes(seed_node_id="", latent_node_id=""))


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"positive_node_id": ""}, "正向 prompt"),
        ({"negative_node_id": ""}, "負向 prompt"),
        ({"output_node_id": ""}, "輸出節點"),
    ],
)
def test_validate_rejects_unset_required_points(workflow, overrides, expected):
    with pytest.raises(ConfigurationError) as exc:
        validate(workflow, make_nodes(**overrides))

    assert expected in str(exc.value)
    assert "未設定" in str(exc.value)


def test_validate_reports_missing_node_with_id_and_env(workflow):
    with pytest.raises(ConfigurationError) as exc:
        validate(workflow, make_nodes(positive_node_id="99"))

    message = str(exc.value)
    assert "正向 prompt" in message
    assert "COMFYUI_POSITIVE_NODE_ID=99" in message
    assert "'99'" in message
    # 訊息要能直接對照現有節點，否則使用者得自己開 JSON 找
    assert "3, 5, 6, 7, 9" in message


def test_validate_reports_missing_field_with_available_fields(workflow):
    with pytest.raises(ConfigurationError) as exc:
        validate(workflow, make_nodes(positive_field="prompt"))

    message = str(exc.value)
    assert "COMFYUI_POSITIVE_FIELD=prompt" in message
    assert "text" in message


def test_validate_checks_optional_points_once_set(workflow):
    """設了卻對不上，是打錯字，不是「不想用」——照必填的標準擋下。"""
    with pytest.raises(ConfigurationError, match="latent 寬度"):
        validate(workflow, make_nodes(width_field="w"))


def test_validate_output_node_needs_no_field(workflow):
    """輸出節點只要求存在：生成後從 /history 取它的圖，不注入任何欄位。"""
    workflow["9"] = {"class_type": "SaveImage"}

    validate(workflow, make_nodes())


def test_validate_rejects_node_without_inputs(workflow):
    workflow["6"] = {"class_type": "CLIPTextEncode"}

    with pytest.raises(ConfigurationError, match="沒有 inputs 物件"):
        validate(workflow, make_nodes())


def test_validate_rejects_non_object_node(workflow):
    workflow["6"] = "CLIPTextEncode"

    with pytest.raises(ConfigurationError, match="不是物件"):
        validate(workflow, make_nodes())


# ── inject ───────────────────────────────────────────────────────


def test_inject_writes_all_points(workflow):
    result = inject(
        workflow,
        make_nodes(),
        positive="a tiger among cats",
        negative="text, watermark",
        seed=42,
        width=768,
        height=432,
    )

    assert result["6"]["inputs"]["text"] == "a tiger among cats"
    assert result["7"]["inputs"]["text"] == "text, watermark"
    assert result["3"]["inputs"]["seed"] == 42
    assert result["5"]["inputs"]["width"] == 768
    assert result["5"]["inputs"]["height"] == 432


def test_inject_keeps_untouched_fields(workflow):
    result = inject(workflow, make_nodes(), positive="p", negative="n", seed=1)

    assert result["3"]["inputs"]["steps"] == 20
    assert result["9"]["inputs"]["filename_prefix"] == "ComfyUI"


def test_inject_does_not_mutate_original(workflow):
    """同一份 workflow 要重複注入上百張卡，就地修改會讓前一張殘留到下一張。"""
    before = json.dumps(workflow, sort_keys=True)

    inject(workflow, make_nodes(), positive="p", negative="n", seed=7, width=768, height=432)

    assert json.dumps(workflow, sort_keys=True) == before


def test_inject_skips_none_values(workflow):
    """seed／尺寸未給就不注入，交由 workflow 自己決定。"""
    result = inject(workflow, make_nodes(), positive="p", negative="n")

    assert result["3"]["inputs"]["seed"] == 1
    assert result["5"]["inputs"]["width"] == 512
    assert result["5"]["inputs"]["height"] == 512


def test_inject_skips_unset_optional_nodes(workflow):
    """節點 ID 留空時，即使給了 seed 值也不該注入。"""
    result = inject(
        workflow, make_nodes(seed_node_id=""), positive="p", negative="n", seed=42
    )

    assert result["3"]["inputs"]["seed"] == 1


def test_inject_allows_empty_negative(workflow):
    result = inject(workflow, make_nodes(), positive="p", negative="")

    assert result["7"]["inputs"]["text"] == ""


def test_inject_validates_before_writing(workflow):
    with pytest.raises(ConfigurationError, match="COMFYUI_POSITIVE_NODE_ID=99"):
        inject(workflow, make_nodes(positive_node_id="99"), positive="p", negative="n")
