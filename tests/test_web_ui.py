"""Gradio 介面的可測部分。

介面本身照 phase 文件走人工驗證，但兩件事值得自動化：**介面組得起來**
（元件參數在 Gradio 改版時會失效——6.0 就把 `gr.Dataframe` 的 `col_count`
換成 `column_count`），以及**進度文字的格式**是純函式。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from anki_deck_builder.config import Settings, load_settings
from anki_deck_builder.state import CardStore
from anki_deck_builder.web import service, ui
from anki_deck_builder.web.tasks import StageRunner


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-value")
    monkeypatch.setenv("YAML_SETTINGS_FILE", "agents.yaml")
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    return monkeypatch


@pytest.fixture
def settings() -> Settings:
    return load_settings()


def test_ui_builds(settings: Settings, tmp_path: Path) -> None:
    """元件參數在 Gradio 改版時會靜靜失效，組一次就能擋下。"""
    blocks = ui.create_ui(settings, CardStore(tmp_path / "cards.csv"), StageRunner())

    assert blocks is not None


def test_run_buttons_are_all_runnable_stages() -> None:
    """按鈕上的階段名必須是 service 認得的，否則按下去只會拿到 400。"""
    for stage, _label in ui.RUN_BUTTONS:
        assert stage in service.RUNNABLE_STAGES


def _report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "stage": "image",
        "running": False,
        "elapsed": None,
        "error": None,
        "results": [],
        "counts": {"pending": 5, "done": 3, "failed": 2},
    }
    report.update(overrides)
    return report


def test_progress_shows_done_over_total() -> None:
    text = ui._format_progress(_report())

    assert "done 3／10" in text and "failed 2" in text


def test_progress_shows_elapsed_while_running() -> None:
    text = ui._format_progress(_report(running=True, elapsed=42.4))

    assert "執行中 42s" in text


def test_progress_shows_the_error() -> None:
    """背景任務的例外沒有人接，UI 不顯示就等於靜靜失敗。"""
    text = ui._format_progress(_report(error="RuntimeError: ComfyUI 沒開"))

    assert "ComfyUI 沒開" in text


def test_progress_summarises_both_audio_sides() -> None:
    text = ui._format_progress(
        _report(
            stage="audio",
            results=[
                {"stage": "audio_front", "succeeded": 8, "failed": 0},
                {"stage": "audio_back", "succeeded": 7, "failed": 1},
            ],
        )
    )

    assert "audio_front 成功 8" in text and "audio_back 成功 7／失敗 1" in text


def test_column_widths_cover_every_editable_column() -> None:
    """寬度少給一欄，Gradio 會把剩下的欄壓成剛好塞滿視窗（橫向捲動消失），
    而凍結欄的位移也會對到錯的欄。長度不符必須當場失敗。"""
    assert len(ui.COLUMN_WIDTHS) == len(ui.EDIT_COLUMNS)


def test_pinned_offsets_are_cumulative_widths() -> None:
    """`left` 位移是它前面所有凍結欄的寬度總和——算錯就會蓋住或露出半欄。"""
    widths = ui.COLUMN_WIDTHS[: ui.PINNED_COUNT]

    assert ui._pinned_offsets() == [sum(widths[:i]) for i in range(len(widths))]


def test_pinned_css_is_actually_rendered_into_the_page(
    settings: Settings, tmp_path: Path
) -> None:
    """凍結欄的 CSS 走元件塞進頁面，不走 `css=` 參數——後者在 Gradio 6 上
    **兩條官方路徑都會被靜默丟掉**（見 `ui` 的模組 docstring）。這個測試盯的
    就是「樣式有沒有真的到得了頁面」，Gradio 再改版時會在這裡失敗。"""
    blocks = ui.create_ui(settings, CardStore(tmp_path / "cards.csv"), StageRunner())

    rendered = [
        block
        for block in blocks.blocks.values()
        if ui.STYLE_HOLDER_CLASS in (getattr(block, "elem_classes", None) or [])
    ]

    assert len(rendered) == 1
    assert "position: sticky" in rendered[0].value
