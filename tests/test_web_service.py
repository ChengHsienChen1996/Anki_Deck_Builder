"""Web 膠水層單元測試。

`service.py` 是 FastAPI 與 Gradio 共用的唯一入口，所以「狀態連動」「篩選」
「金鑰遮罩」這些行為在這裡測一次就對兩個介面都成立。

**階段一律以假物件替換**：真的跑 image／audio 會載模型、佔 VRAM、打外部服務。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from anki_deck_builder.config import Settings
from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.state import CardStore
from anki_deck_builder.web import service
from anki_deck_builder.web.tasks import StageBusyError, StageRunner

from .conftest import clear_project_env


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    """同 `test_cli.py`：關進空目錄與乾淨環境，別讀到開發者的真實 `.env`。"""
    monkeypatch.chdir(tmp_path)
    clear_project_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-value")
    monkeypatch.setenv("YAML_SETTINGS_FILE", "agents.yaml")
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    return monkeypatch


@pytest.fixture
def settings() -> Settings:
    from anki_deck_builder.config import load_settings

    return load_settings()


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "work" / "cards.csv")


async def _seed(store: CardStore, *rows: CardRow) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    await store.write(list(rows))


def _card(card_id: str = "p1_001", **overrides: Any) -> CardRow:
    values: dict[str, Any] = {
        "card_id": card_id,
        "deck": "日語::N2",
        "front": "属する",
        "image_prompt": "a tiger among cats",
        "tts_front_text": "ぞくする",
        "tts_back_text": "彼は野球部に属する。",
        "extract_status": StageStatus.DONE,
        "image_status": StageStatus.DONE,
        "audio_front_status": StageStatus.DONE,
        "audio_back_status": StageStatus.DONE,
    }
    values.update(overrides)
    return CardRow(**values)


# ── 讀取 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_summary_matches_stage_counts(store: CardStore) -> None:
    await _seed(store, _card("a"), _card("b", image_status=StageStatus.FAILED))

    summary = await service.status_summary(store)

    assert summary["image"] == {"pending": 0, "done": 1, "failed": 1}


@pytest.mark.asyncio
async def test_status_summary_by_kind_separates_source_rows_from_cards(
    store: CardStore,
) -> None:
    """來源列與卡片列分開統計，兩邊相加等於全部。

    `extract` 是一對多：一列 `raw_text` 產出多張卡，而來源列會留在檔案裡。
    合在一起數會讓「共 N 列」和抽取結果分頁的「N 張卡」對不上，也會讓
    `ocr done` 看起來像做了 N 次辨識（實際只有來源列那幾次）。
    """
    await _seed(
        store,
        CardRow(source="/imgs/p1.jpg", ocr_source_page=1, raw_text="一頁的文字"),
        _card("p1_001"),
        _card("p1_002", image_status=StageStatus.FAILED),
    )

    summary = await service.status_summary_by_kind(store)
    whole = await service.status_summary(store)

    assert summary["source"]["rows"] == 1
    assert summary["cards"]["rows"] == 2
    assert summary["cards"]["stages"]["image"] == {"pending": 0, "done": 1, "failed": 1}
    for stage, counts in whole.items():
        for status, total in counts.items():
            assert total == (
                summary["source"]["stages"][stage][status]
                + summary["cards"]["stages"][stage][status]
            ), f"{stage}/{status} 兩邊相加對不上全部"


@pytest.mark.asyncio
async def test_missing_work_file_is_empty_not_an_error(store: CardStore) -> None:
    """工作檔還沒建立時 UI 也要開得起來。"""
    assert (await service.list_rows(store)).total == 0
    assert await service.failed_list(store) == []


@pytest.mark.asyncio
async def test_list_rows_skips_source_rows(store: CardStore) -> None:
    """`card_id` 為空的是 ocr／extract 的來源列，不是卡片。"""
    await _seed(store, _card("a"), CardRow(raw_text="整頁文字"))

    page = await service.list_rows(store)

    assert page.total == 1 and page.rows[0]["card_id"] == "a"


@pytest.mark.asyncio
async def test_list_rows_paginates(store: CardStore) -> None:
    await _seed(store, *(_card(f"c{i}") for i in range(10)))

    page = await service.list_rows(store, offset=4, limit=3)

    assert page.total == 10
    assert [row["card_id"] for row in page.rows] == ["c4", "c5", "c6"]


@pytest.mark.asyncio
async def test_list_rows_filters_by_stage_status(store: CardStore) -> None:
    await _seed(store, _card("a"), _card("b", image_status=StageStatus.FAILED))

    page = await service.list_rows(store, stage="image", status="failed")

    assert [row["card_id"] for row in page.rows] == ["b"]


@pytest.mark.asyncio
async def test_status_filter_requires_stage(store: CardStore) -> None:
    """`audio` 有兩個狀態欄位，不指定階段就無從篩起。"""
    await _seed(store, _card("a"))

    with pytest.raises(service.ServiceError, match="stage"):
        await service.list_rows(store, status="failed")


@pytest.mark.asyncio
async def test_failed_list_reports_stage_and_reason(store: CardStore) -> None:
    await _seed(
        store,
        _card("a", audio_back_status=StageStatus.FAILED, audio_back_error="沒有文字"),
    )

    failures = await service.failed_list(store)

    assert failures == [
        {"card_id": "a", "stage": "audio_back", "error": "沒有文字", "front": "属する"}
    ]


def test_config_view_masks_the_api_key(settings: Settings) -> None:
    view = service.config_view(settings)

    assert "****" in view["agent_factory"]["openai_api_key"]
    assert "sk-test-key-value" not in str(view)


# ── 媒體 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_media_file_resolves_registered_path(store: CardStore) -> None:
    await _seed(store, _card("a", image_front="media/img/a.png"))
    image = store.path.parent / "media" / "img" / "a.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")

    assert await service.media_file(store, "a", "image_front") == image.resolve()


@pytest.mark.asyncio
async def test_media_file_rejects_path_traversal(store: CardStore) -> None:
    """CSV 是使用者可編輯的資料，`../` 不能變成讀取任意檔案的管道。"""
    secret = store.path.parent.parent / "secret.txt"
    secret.write_text("內容")
    await _seed(store, _card("a", image_front="../secret.txt"))

    with pytest.raises(service.ServiceError):
        await service.media_file(store, "a", "image_front")


@pytest.mark.asyncio
async def test_media_file_rejects_non_media_field(store: CardStore) -> None:
    await _seed(store, _card("a"))

    with pytest.raises(service.ServiceError, match="不是媒體欄位"):
        await service.media_file(store, "a", "front")


# ── 失敗清單 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failed_list_can_filter_by_stage(store: CardStore) -> None:
    await _seed(
        store,
        _card("a", image_status=StageStatus.FAILED, image_error="沒有 prompt"),
        _card("b", audio_back_status=StageStatus.FAILED, audio_back_error="沒有文字"),
    )

    assert [f["card_id"] for f in await service.failed_list(store, "image")] == ["a"]


@pytest.mark.asyncio
async def test_one_row_failing_two_stages_appears_twice(store: CardStore) -> None:
    """兩側語音是各自獨立的失敗，要各自重跑。"""
    await _seed(
        store,
        _card(
            "a",
            audio_front_status=StageStatus.FAILED,
            audio_front_error="正面壞了",
            audio_back_status=StageStatus.FAILED,
            audio_back_error="背面壞了",
        ),
    )

    failures = await service.failed_list(store)

    assert [f["stage"] for f in failures] == ["audio_front", "audio_back"]


@pytest.mark.asyncio
async def test_failed_stages_follow_pipeline_order(store: CardStore) -> None:
    await _seed(
        store,
        _card("a", image_status=StageStatus.FAILED),
        _card("b", extract_status=StageStatus.FAILED),
    )

    assert await service.failed_stages(store) == ["extract", "image"]


@pytest.mark.asyncio
async def test_failed_stages_merge_both_audio_sides(store: CardStore) -> None:
    """分開跑等於把 77 秒的模型載入付兩次。"""
    await _seed(
        store,
        _card(
            "a",
            audio_front_status=StageStatus.FAILED,
            audio_back_status=StageStatus.FAILED,
        ),
    )

    assert await service.failed_stages(store) == [service.AUDIO_BOTH]


@pytest.mark.asyncio
async def test_run_failed_runs_each_stage_with_only_failed(
    store: CardStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(
        store,
        _card("a", image_status=StageStatus.FAILED),
        _card("b", extract_status=StageStatus.FAILED),
    )
    seen: list[tuple[str, dict[str, Any]]] = []

    async def fake_run(settings_, store_, stage, **kwargs):  # noqa: ANN001, ANN202
        seen.append((stage, kwargs))
        return []

    monkeypatch.setattr(service, "run_stage", fake_run)

    await service.run_failed(settings, store)

    assert seen == [
        ("extract", {"only_failed": True}),
        ("image", {"only_failed": True}),
    ]


@pytest.mark.asyncio
async def test_run_failed_honours_an_explicit_stage(
    store: CardStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(store, _card("a", image_status=StageStatus.FAILED))
    seen: list[str] = []

    async def fake_run(settings_, store_, stage, **kwargs):  # noqa: ANN001, ANN202
        seen.append(stage)
        return []

    monkeypatch.setattr(service, "run_stage", fake_run)

    await service.run_failed(settings, store, "image")

    assert seen == ["image"]


@pytest.mark.asyncio
async def test_all_failed_progress_sums_every_stage(store: CardStore) -> None:
    """跨階段的批次重跑沒有單一階段可查，數字要加總而不是炸掉。"""
    await _seed(store, _card("a", image_status=StageStatus.FAILED))

    report = await service.stage_progress(store, service.ALL_FAILED, None)

    # 七個階段各一列：ocr／scene／prompt 仍 pending、extract 與兩側語音 done、image failed
    assert report["counts"] == {"pending": 3, "done": 3, "failed": 1}


# ── 縮圖牆 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gallery_resolves_existing_images(store: CardStore) -> None:
    await _seed(store, _card("a", image_front="media/img/a.png"))
    image = store.path.parent / "media" / "img" / "a.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")

    _total, entries = await service.image_gallery(store)

    assert entries[0].image == image.resolve()
    assert entries[0].front == "属する" and entries[0].status == "done"


@pytest.mark.asyncio
async def test_gallery_includes_cards_without_images(store: CardStore) -> None:
    """缺圖的卡也要列出來，否則就再也點不到、生不出來了。"""
    await _seed(store, _card("a", image_front="", image_status=StageStatus.PENDING))

    _total, entries = await service.image_gallery(store)

    assert entries[0].image is None and entries[0].status == "pending"


@pytest.mark.asyncio
async def test_gallery_treats_missing_file_as_ungenerated(store: CardStore) -> None:
    """CSV 說有圖但檔案被刪了——當成沒生成，而不是拋錯讓整頁開不起來。"""
    await _seed(store, _card("a", image_front="media/img/gone.png"))

    _total, entries = await service.image_gallery(store)

    assert entries[0].image is None


@pytest.mark.asyncio
async def test_gallery_refuses_paths_outside_the_media_root(store: CardStore) -> None:
    secret = store.path.parent.parent / "secret.png"
    secret.write_bytes(b"png")
    await _seed(store, _card("a", image_front="../secret.png"))

    _total, entries = await service.image_gallery(store)

    assert entries[0].image is None


@pytest.mark.asyncio
async def test_gallery_paginates_and_skips_source_rows(store: CardStore) -> None:
    await _seed(store, *(_card(f"c{i}") for i in range(5)), CardRow(raw_text="來源列"))

    total, entries = await service.image_gallery(store, offset=2, limit=2)

    assert total == 5
    assert [e.card_id for e in entries] == ["c2", "c3"]


# ── 編輯與狀態連動 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_editing_only_content_fields_still_persists(store: CardStore) -> None:
    """只改內容欄位（不觸發任何階段重置）仍然要寫回檔案。

    `update_rows` 曾經拿「有沒有重置階段」當「這列有沒有改動」的代理。那只在
    「任何內容改動都會重置 `extract`」成立時才等價——`_ALWAYS_RESET` 清空之後
    就不等價了，只改 `front`／`back` 會讓整批不寫檔，**編輯靜默消失**。
    """
    await _seed(store, _card("a"))

    result = await service.update_rows(store, {"a": {"back": "改過的釋義"}})

    assert result["updated"] == ["a"]
    assert result["reset"] == []
    rows = await store.read()
    assert next(r for r in rows if r.card_id == "a").back == "改過的釋義"


@pytest.mark.asyncio
async def test_editing_content_leaves_extract_alone(store: CardStore) -> None:
    """人工修正**不會**把 `extract_status` 設回 `pending`。

    原本會，用意是標記「這列被人改過」——但 `extract` 對已產出的卡片列是
    no-op，跑一次只會把狀態翻回 `done`，標記撐不過它自己聲稱要觸發的操作。
    2026-09-06 為了重跑一頁而執行 extract 時，得先快照 70 張人工校對過的卡
    再還原，否則紀錄被無聲抹掉；同時狀態總覽的「pending 71」也把唯一真的
    要跑的那一列淹掉了。理由完整寫在 `service._ALWAYS_RESET`。
    """
    await _seed(store, _card("a"))

    updated = await service.update_row(store, "a", {"front": "属す"})

    assert updated["extract_status"] == "done"
    assert updated["image_status"] == "done"


@pytest.mark.asyncio
async def test_editing_image_prompt_also_resets_image(store: CardStore) -> None:
    await _seed(store, _card("a"))

    updated = await service.update_row(store, "a", {"image_prompt": "a lone wolf"})

    assert updated["extract_status"] == "done"
    assert updated["image_status"] == "pending"


@pytest.mark.asyncio
async def test_editing_back_text_leaves_front_audio_alone(store: CardStore) -> None:
    """兩側是兩個獨立階段——改背面文字不該讓正面的語音重跑。"""
    await _seed(store, _card("a"))

    updated = await service.update_row(store, "a", {"tts_back_text": "新的例句。"})

    assert updated["audio_back_status"] == "pending"
    assert updated["audio_front_status"] == "done"


@pytest.mark.asyncio
async def test_reset_clears_the_stale_error(store: CardStore) -> None:
    await _seed(store, _card("a", image_status=StageStatus.FAILED, image_error="舊錯誤"))

    updated = await service.update_row(store, "a", {"image_prompt": "new"})

    assert updated["image_error"] == ""


@pytest.mark.asyncio
async def test_unchanged_value_resets_nothing(store: CardStore) -> None:
    """按了儲存但什麼都沒改，不該讓整批重跑。"""
    await _seed(store, _card("a"))

    updated = await service.update_row(store, "a", {"front": "属する"})

    assert updated["extract_status"] == "done"


@pytest.mark.asyncio
async def test_status_fields_are_not_editable(store: CardStore) -> None:
    """狀態由階段骨架維護，開放給 Web 直接寫等於讓 UI 繞過狀態機。"""
    await _seed(store, _card("a"))

    with pytest.raises(service.ServiceError, match="image_status"):
        await service.update_row(store, "a", {"image_status": "done"})


@pytest.mark.asyncio
async def test_media_paths_are_not_editable(store: CardStore) -> None:
    await _seed(store, _card("a"))

    with pytest.raises(service.ServiceError, match="audio_front"):
        await service.update_row(store, "a", {"audio_front": "/etc/passwd"})


@pytest.mark.asyncio
async def test_updating_unknown_card_is_reported(store: CardStore) -> None:
    await _seed(store, _card("a"))

    with pytest.raises(service.ServiceError, match="找不到卡片"):
        await service.update_row(store, "zzz", {"front": "x"})


@pytest.mark.asyncio
async def test_edit_persists_to_disk(store: CardStore) -> None:
    await _seed(store, _card("a"))

    await service.update_row(store, "a", {"front": "属す"})

    assert (await store.read())[0].front == "属す"


# ── 批次編輯（表格儲存）─────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_rows_applies_each_row(store: CardStore) -> None:
    await _seed(store, _card("a"), _card("b"))

    result = await service.update_rows(
        store,
        {
            "a": {"front": "属す"},
            "b": {"image_prompt": "a lone wolf"},
        },
    )

    assert result["updated"] == ["a", "b"]
    # 改 front 不觸發任何階段（`extract` 不再重置），改 image_prompt 觸發 image
    assert set(result["reset"]) == {"image"}


@pytest.mark.asyncio
async def test_update_rows_ignores_untouched_rows(store: CardStore) -> None:
    """表格儲存會把整頁送上來，沒改的列不該被標成 pending。"""
    await _seed(store, _card("a"), _card("b"))

    result = await service.update_rows(
        store, {"a": {"front": "属する"}, "b": {"front": "属する"}}
    )

    assert result["updated"] == []
    assert (await store.read())[0].extract_status is StageStatus.DONE


@pytest.mark.asyncio
async def test_update_rows_cascades_per_row(store: CardStore) -> None:
    """A 改例句、B 改圖 prompt，兩列各自重置各自的階段。"""
    await _seed(store, _card("a"), _card("b"))

    await service.update_rows(
        store, {"a": {"tts_back_text": "新例句。"}, "b": {"image_prompt": "new"}}
    )

    rows = {row.card_id: row for row in await store.read()}
    assert rows["a"].audio_back_status is StageStatus.PENDING
    assert rows["a"].image_status is StageStatus.DONE
    assert rows["b"].image_status is StageStatus.PENDING
    assert rows["b"].audio_back_status is StageStatus.DONE


@pytest.mark.asyncio
async def test_update_rows_writes_once(store: CardStore, monkeypatch) -> None:
    """逐列寫檔 = 整份 CSV 重新序列化 N 次，中途失敗還會留下半套狀態。"""
    await _seed(store, *(_card(f"c{i}") for i in range(5)))
    writes = 0
    original = CardStore.write

    async def counting_write(self, rows):  # noqa: ANN001, ANN202
        nonlocal writes
        writes += 1
        await original(self, rows)

    monkeypatch.setattr(CardStore, "write", counting_write)

    await service.update_rows(store, {f"c{i}": {"front": f"新 {i}"} for i in range(5)})

    assert writes == 1


@pytest.mark.asyncio
async def test_update_rows_rejects_readonly_fields_before_writing(
    store: CardStore,
) -> None:
    """一列不合法就整批不寫，不留半套。"""
    await _seed(store, _card("a"), _card("b"))

    with pytest.raises(service.ServiceError, match="image_status"):
        await service.update_rows(
            store, {"a": {"front": "属す"}, "b": {"image_status": "done"}}
        )

    assert (await store.read())[0].front == "属する"


@pytest.mark.asyncio
async def test_update_rows_reports_unknown_card(store: CardStore) -> None:
    await _seed(store, _card("a"))

    with pytest.raises(service.ServiceError, match="找不到卡片"):
        await service.update_rows(store, {"zzz": {"front": "x"}})


@pytest.mark.asyncio
async def test_deck_names_are_sorted_and_unique(store: CardStore) -> None:
    await _seed(
        store,
        _card("a", deck="日語::N2"),
        _card("b", deck="english::vocabulary"),
        _card("c", deck="日語::N2"),
        CardRow(raw_text="來源列"),
    )

    assert await service.deck_names(store) == ["english::vocabulary", "日語::N2"]


# ── 匯入 ─────────────────────────────────────────────────────────


def _png(path: Path) -> Path:
    """真的 PNG——`detect_input` 只看副檔名，但保持與階段一致比較安全。"""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())
    return path


@pytest.mark.asyncio
async def test_preview_lists_images_in_natural_order(tmp_path: Path) -> None:
    """`page2` 要排在 `page10` 前面——順序錯了 ocr_source_page 就跟著錯。"""
    pages = tmp_path / "scans"
    pages.mkdir()
    for name in ("page10.png", "page2.png", "page1.png"):
        _png(pages / name)

    result = await service.preview_input(str(pages))

    assert result["kind"] == "image_dir" and result["total"] == 3
    assert result["items"] == ["page1.png", "page2.png", "page10.png"]


@pytest.mark.asyncio
async def test_preview_writes_nothing(tmp_path: Path, store: CardStore) -> None:
    pages = tmp_path / "scans"
    pages.mkdir()
    _png(pages / "a.png")

    await service.preview_input(str(pages))

    assert not store.exists()


@pytest.mark.asyncio
async def test_preview_truncates_long_listings(tmp_path: Path) -> None:
    pages = tmp_path / "scans"
    pages.mkdir()
    for i in range(25):
        _png(pages / f"p{i:03d}.png")

    result = await service.preview_input(str(pages))

    assert result["total"] == 25
    assert len(result["items"]) == service.PREVIEW_LIMIT
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_preview_reports_an_unsupported_path(tmp_path: Path) -> None:
    bad = tmp_path / "note.docx"
    bad.write_text("x")

    with pytest.raises(service.ServiceError, match="支援"):
        await service.preview_input(str(bad))


@pytest.mark.asyncio
async def test_preview_reports_a_missing_path(tmp_path: Path) -> None:
    with pytest.raises(service.ServiceError):
        await service.preview_input(str(tmp_path / "不存在"))


@pytest.mark.asyncio
async def test_ingest_creates_one_row_per_image(
    tmp_path: Path, store: CardStore, settings: Settings
) -> None:
    pages = tmp_path / "scans"
    pages.mkdir()
    for name in ("a.png", "b.png"):
        _png(pages / name)

    result = await service.ingest_path(settings, store, str(pages))

    assert result == {"kind": "image_dir", "created": 2, "skipped": 0, "total": 2}
    assert len(await store.read()) == 2


@pytest.mark.asyncio
async def test_ingesting_twice_does_not_duplicate(
    tmp_path: Path, store: CardStore, settings: Settings
) -> None:
    pages = tmp_path / "scans"
    pages.mkdir()
    _png(pages / "a.png")
    await service.ingest_path(settings, store, str(pages))

    result = await service.ingest_path(settings, store, str(pages))

    assert result["created"] == 0 and result["skipped"] == 1
    assert len(await store.read()) == 1


@pytest.mark.asyncio
async def test_ingest_is_refused_while_a_stage_runs(
    tmp_path: Path, store: CardStore, settings: Settings
) -> None:
    """匯入要寫工作檔，與階段的 checkpoint 會打架。"""
    pages = tmp_path / "scans"
    pages.mkdir()
    _png(pages / "a.png")
    runner = StageRunner()
    gate = asyncio.Event()
    runner.start("ocr", gate.wait)

    with pytest.raises(StageBusyError, match="ocr"):
        await service.ingest_path(settings, store, str(pages), runner=runner)

    gate.set()
    await runner.wait()
    assert not store.exists()


# ── 重置 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_stages_only_touches_those_stages(store: CardStore) -> None:
    await _seed(store, _card("a"))

    result = await service.reset_work(store, "stages", stages=["image"])

    rows = await store.read()
    assert rows[0].image_status is StageStatus.PENDING
    assert rows[0].audio_front_status is StageStatus.DONE
    assert result["affected_rows"] == 1 and result["backup"]


@pytest.mark.asyncio
async def test_reset_rejects_unknown_stage(store: CardStore) -> None:
    await _seed(store, _card("a"))

    with pytest.raises(service.ServiceError, match="audio_front"):
        await service.reset_work(store, "stages", stages=["audio"])


@pytest.mark.asyncio
async def test_reset_rejects_unknown_mode(store: CardStore) -> None:
    await _seed(store, _card("a"))

    with pytest.raises(service.ServiceError, match="未知的重置模式"):
        await service.reset_work(store, "everything")


@pytest.mark.asyncio
async def test_clearing_needs_the_work_file_name(store: CardStore) -> None:
    """「確定嗎？」按下去只需要一次手滑；打出檔名則需要看清楚自己在做什麼。"""
    await _seed(store, _card("a"))

    with pytest.raises(service.ServiceError, match="cards.csv"):
        await service.reset_work(store, "rows", confirm="yes")

    assert len(await store.read()) == 1


@pytest.mark.asyncio
async def test_clear_rows_keeps_media(store: CardStore) -> None:
    await _seed(store, _card("a"))
    image = store.path.parent / "media" / "img" / "a.webp"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"webp")

    result = await service.reset_work(store, "rows", confirm="cards.csv")

    assert result["cleared_rows"] == 1 and result["removed_media"] == 0
    assert image.is_file()


@pytest.mark.asyncio
async def test_clear_all_removes_media(store: CardStore) -> None:
    await _seed(store, _card("a"))
    for name in ("media/img/a.webp", "media/audio/a_front.mp3"):
        path = store.path.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    result = await service.reset_work(store, "all", confirm="cards.csv")

    assert result["removed_media"] == 2
    assert await store.read() == []


@pytest.mark.asyncio
async def test_reset_is_refused_while_a_stage_runs(store: CardStore) -> None:
    """清空會與階段的 checkpoint 寫入打架。"""
    await _seed(store, _card("a"))
    runner = StageRunner()
    gate = asyncio.Event()
    runner.start("image", gate.wait)

    with pytest.raises(StageBusyError, match="image"):
        await service.reset_work(store, "rows", confirm="cards.csv", runner=runner)

    gate.set()
    await runner.wait()
    assert len(await store.read()) == 1


# ── 執行 ─────────────────────────────────────────────────────────


class FakeStage:
    """記錄 `run()` 收到什麼的假階段。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    async def run(self, store: CardStore, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        from anki_deck_builder.stages.base import StageResult

        return StageResult(
            stage=self.name, processed=1, succeeded=1, failed=0, added=0
        )


@pytest.mark.asyncio
async def test_run_stage_passes_selection_through(
    store: CardStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """web 不自己篩列——`card_ids` 原封不動交給階段（骨架的擴充參數）。"""
    stage = FakeStage("image")
    monkeypatch.setattr(service, "_prepare", _prepared(stage))

    results = await service.run_stage(
        settings, store, "image", only_failed=True, card_ids=["a", "b"]
    )

    assert stage.calls == [
        {"force": False, "only_failed": True, "card_ids": ["a", "b"]}
    ]
    assert [r.stage for r in results] == ["image"]


@pytest.mark.asyncio
async def test_audio_alias_runs_both_sides(
    store: CardStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UI 上「生成語音」是一顆按鈕，但底下是兩個獨立階段。"""
    front, back = FakeStage("audio_front"), FakeStage("audio_back")
    monkeypatch.setattr(service, "_prepare", _prepared(front, back))

    results = await service.run_stage(settings, store, "audio")

    assert [r.stage for r in results] == ["audio_front", "audio_back"]


@pytest.mark.asyncio
async def test_prepare_audio_asks_comfyui_to_free_vram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """web 的 VRAM 讓渡順序要與 `cli.py` 完全相同——audio 前同樣得請 ComfyUI 讓位。

    其他測試把 `_prepare` 整個換成假的，這條就沒人守；而 kyoani workflow
    常駐 17.4 GB ＋ VOXCPM2 峰值 7.5 GB 會超出 24 GB 卡，漏掉這一步是實跑才會炸。
    """
    from anki_deck_builder.config import load_settings

    monkeypatch.setenv("COMFYUI_FREE_BEFORE_LLM", "true")
    seen: list[str] = []

    async def spy(base_url: str, **kwargs: Any) -> bool:
        seen.append(base_url)
        return True

    monkeypatch.setattr("anki_deck_builder.clients.comfyui_client.free_memory", spy)
    monkeypatch.setattr(service, "build_audio_stages", lambda *a, **k: [])

    assert await service._prepare(load_settings(), "audio_front") == []
    assert seen == ["http://127.0.0.1:8188"]


@pytest.mark.asyncio
async def test_prepare_image_does_not_ask_comfyui_to_free_vram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """image 正要用 ComfyUI，清空它只是白費工。"""
    from anki_deck_builder.config import load_settings

    monkeypatch.setenv("COMFYUI_FREE_BEFORE_LLM", "true")
    seen: list[str] = []

    async def spy(base_url: str, **kwargs: Any) -> bool:
        seen.append(base_url)
        return True

    monkeypatch.setattr("anki_deck_builder.clients.comfyui_client.free_memory", spy)
    monkeypatch.setattr(service, "build_image_stage", lambda *a, **k: "stage")

    assert await service._prepare(load_settings(), "image") == ["stage"]
    assert seen == []


@pytest.mark.parametrize("stage", ["scene", "prompt"])
@pytest.mark.asyncio
async def test_prepare_text_stages_ask_comfyui_to_free_vram(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scene 與 prompt 都是本地 LLM 階段——讓渡條件與 extract 相同。

    這兩條同時是接線的煙霧測試：`_prepare` 的分支若漏了 import 或打錯 builder，
    只有走過該分支才看得出來（Task 8.3 就漏過一次，靠 ruff 才抓到）。
    """
    from anki_deck_builder.config import load_settings

    monkeypatch.setenv("COMFYUI_FREE_BEFORE_LLM", "true")
    seen: list[str] = []

    async def spy(base_url: str, **kwargs: Any) -> bool:
        seen.append(base_url)
        return True

    monkeypatch.setattr("anki_deck_builder.clients.comfyui_client.free_memory", spy)
    monkeypatch.setattr(service, f"build_{stage}_stage", lambda *a, **k: _FakeTextStage())

    prepared = await service._prepare(load_settings(), stage)

    assert len(prepared) == 1
    assert seen == ["http://127.0.0.1:8188"]


class _FakeTextStage:
    """只提供 `_prepare` 會用到的兩樣東西：client 端點與 agent 名稱。"""

    agent = "ImagePromptFluxAgent"

    class client:  # noqa: N801 - 只是個命名空間
        @staticmethod
        def model_endpoint(agent_name: str) -> tuple[str, str]:
            return ("http://localhost:11434/v1", "gemma4-e4b-optimized:latest")


@pytest.mark.asyncio
async def test_unknown_stage_is_rejected(store: CardStore, settings: Settings) -> None:
    with pytest.raises(service.ServiceError, match="未知的階段"):
        await service.run_stage(settings, store, "pack")


@pytest.mark.asyncio
async def test_run_stage_uses_the_shared_factory(
    store: CardStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """階段組裝走 `stages/factory.py`，與 CLI 同一份——不在 web 裡另建 client。"""
    built: list[str] = []
    monkeypatch.setattr(
        service, "build_image_stage", lambda s: built.append("image") or FakeStage("image")
    )
    monkeypatch.setattr(service, "release_all_gpu", _noop)

    await service.run_stage(settings, store, "image")

    assert built == ["image"]


@pytest.mark.asyncio
async def test_image_run_frees_vram_before_building_the_stage(
    store: CardStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """順序與 CLI 相同：先讓 VRAM，再組階段（驗收第 7 步的「行為一致」）。

    2026-09-10 起讓渡由 `_prepare()` 最前面的 `release_all_gpu()` 統一做，
    因此它跑在組階段**之前**（舊版是 image 組完才讓渡）。
    """
    order: list[str] = []

    async def spy(*args: Any, **kwargs: Any) -> None:
        order.append("free_vram")

    monkeypatch.setattr(service, "release_all_gpu", spy)
    monkeypatch.setattr(
        service,
        "build_image_stage",
        lambda s: order.append("build") or FakeStage("image"),
    )

    await service.run_stage(settings, store, "image")

    assert order == ["free_vram", "build"]


@pytest.mark.asyncio
async def test_image_run_keeps_comfyui(
    store: CardStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """image 正要用 ComfyUI，開跑前的讓渡不該把它清掉（白付一次冷啟）。"""
    seen: list[frozenset[str]] = []

    async def spy(*args: Any, **kwargs: Any) -> None:
        seen.append(frozenset(kwargs["keep"]))

    monkeypatch.setattr(service, "release_all_gpu", spy)
    monkeypatch.setattr(service, "build_image_stage", lambda s: FakeStage("image"))

    await service.run_stage(settings, store, "image")

    assert seen == [frozenset({"comfyui"})]


def _prepared(*stages: FakeStage):  # noqa: ANN202 - 測試用假 _prepare
    async def prepare(settings: Settings, stage: str) -> list[FakeStage]:
        return list(stages)

    return prepare


async def _noop(*args: Any, **kwargs: Any) -> None:
    return None
