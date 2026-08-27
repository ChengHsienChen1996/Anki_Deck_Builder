"""OCR 階段單元測試。

判別、頁序、純文字繞過、失敗跳過等純邏輯**可執行**；OCR 呼叫一律以假 client
替換（依 Protocol 注入），不觸及任何真實服務。
"""

from collections.abc import Sequence
from pathlib import Path

import pytest
from PIL import Image

from anki_deck_builder.exceptions import ExternalServiceError, StageProcessingError
from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.stages import get_stage
from anki_deck_builder.stages.input_source import InputKind
from anki_deck_builder.stages.ocr import OCRStage
from anki_deck_builder.state import CardStore


class FakeOCRClient:
    """記錄每次呼叫，可指定回應或錯誤。"""

    def __init__(self, text: str = "辨識結果", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[str] = []

    async def recognize(self, image_b64: str) -> str:
        self.calls.append(image_b64)
        if self.error is not None:
            raise self.error
        return self.text


class ExplodingOCRClient:
    """任何呼叫都代表測試失敗——用於驗證「不得發出請求」。"""

    async def recognize(self, image_b64: str) -> str:  # pragma: no cover
        raise AssertionError("此情境不應呼叫 OCR client")


def _write_image(path: Path, size: tuple[int, int] = (60, 40)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (220, 220, 220)).save(path)
    return path


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "work" / "cards.csv")


def _stage(client: object, **kwargs: object) -> OCRStage:
    return OCRStage(client=client, **kwargs)  # type: ignore[arg-type]


# ── 註冊與設定 ──────────────────────────────────────────────────


def test_registered_as_ocr() -> None:
    assert get_stage("ocr") is OCRStage


def test_concurrency_is_fixed_at_one() -> None:
    """本地推理 GPU 序列化，併發只會讓多份 KV cache 同時佔 VRAM（執行計畫 §Q5）。"""
    assert _stage(FakeOCRClient()).concurrency == 1


# ── prepare：純文字 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_text_input_is_read_without_calling_ocr(store: CardStore, tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("あきらめる 放棄；死心", encoding="utf-8")
    stage = _stage(ExplodingOCRClient())

    result = await stage.prepare(store, source)

    assert result.kind is InputKind.TEXT
    rows = await store.read()
    assert rows[0].raw_text == "あきらめる 放棄；死心"


@pytest.mark.asyncio
async def test_text_rows_are_marked_done_so_run_skips_them(
    store: CardStore, tmp_path: Path
) -> None:
    """標 done 才能保證 run() 找不到待處理列，真的不發任何請求。"""
    source = tmp_path / "notes.txt"
    source.write_text("內容", encoding="utf-8")
    stage = _stage(ExplodingOCRClient())
    await stage.prepare(store, source)

    result = await stage.run(store)

    assert result.processed == 0
    assert (await store.read())[0].ocr_status is StageStatus.DONE


@pytest.mark.asyncio
async def test_markdown_input_is_treated_as_text(store: CardStore, tmp_path: Path) -> None:
    source = tmp_path / "notes.md"
    source.write_text("# 標題", encoding="utf-8")

    result = await _stage(ExplodingOCRClient()).prepare(store, source)

    assert result.kind is InputKind.TEXT


# ── prepare：影像 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_image_creates_one_pending_row(store: CardStore, tmp_path: Path) -> None:
    source = _write_image(tmp_path / "page.jpg")

    result = await _stage(FakeOCRClient()).prepare(store, source)

    assert (result.kind, result.created) == (InputKind.IMAGE, 1)
    row = (await store.read())[0]
    assert row.ocr_status is StageStatus.PENDING
    assert row.raw_text == ""
    assert row.source == str(source)


@pytest.mark.asyncio
async def test_image_dir_page_numbers_follow_natural_order(
    store: CardStore, tmp_path: Path
) -> None:
    pages = tmp_path / "pages"
    for index in (1, 2, 10):
        _write_image(pages / f"page{index}.jpg")

    await _stage(FakeOCRClient()).prepare(store, pages)

    rows = await store.read()
    assert [r.ocr_source_page for r in rows] == [1, 2, 3]
    assert [Path(r.source).name for r in rows] == ["page1.jpg", "page2.jpg", "page10.jpg"]


@pytest.mark.asyncio
async def test_creates_csv_when_missing(store: CardStore, tmp_path: Path) -> None:
    assert not store.exists()

    await _stage(FakeOCRClient()).prepare(store, _write_image(tmp_path / "page.jpg"))

    assert store.exists()


@pytest.mark.asyncio
async def test_existing_rows_are_kept(store: CardStore, tmp_path: Path) -> None:
    await store.write([CardRow(card_id="old_001", front="既有卡")])

    await _stage(FakeOCRClient()).prepare(store, _write_image(tmp_path / "page.jpg"))

    rows = await store.read()
    assert [r.card_id for r in rows] == ["old_001", ""]


@pytest.mark.asyncio
async def test_rerunning_prepare_does_not_duplicate_rows(
    store: CardStore, tmp_path: Path
) -> None:
    source = _write_image(tmp_path / "page.jpg")
    stage = _stage(FakeOCRClient())
    await stage.prepare(store, source)

    result = await stage.prepare(store, source)

    assert (result.created, result.skipped) == (0, 1)
    assert len(await store.read()) == 1


@pytest.mark.asyncio
async def test_unsupported_input_raises(store: CardStore, tmp_path: Path) -> None:
    bad = tmp_path / "scan.tiff"
    bad.write_text("x", encoding="utf-8")

    with pytest.raises(StageProcessingError):
        await _stage(FakeOCRClient()).prepare(store, bad)


# ── prepare：PDF ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pdf_pages_are_rendered_and_ordered(store: CardStore, tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    pages = [Image.new("RGB", (300, 400), (250 - i * 30,) * 3) for i in range(3)]
    pages[0].save(pdf, save_all=True, append_images=pages[1:])

    result = await _stage(FakeOCRClient(), work_dir=tmp_path / "work").prepare(store, pdf)

    assert (result.kind, result.created) == (InputKind.PDF, 3)
    rows = await store.read()
    assert [r.ocr_source_page for r in rows] == [1, 2, 3]
    assert [Path(r.source).name for r in rows] == [
        "page_0001.png",
        "page_0002.png",
        "page_0003.png",
    ]


# ── run：辨識 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_fills_raw_text(store: CardStore, tmp_path: Path) -> None:
    client = FakeOCRClient(text="□増大\nぞうだい")
    stage = _stage(client)
    await stage.prepare(store, _write_image(tmp_path / "page.jpg"))

    result = await stage.run(store)

    assert (result.succeeded, result.failed) == (1, 0)
    assert (await store.read())[0].raw_text == "□増大\nぞうだい"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_failure_is_recorded_and_batch_continues(
    store: CardStore, tmp_path: Path
) -> None:
    """一張壞圖不能拖垮整批（約束 3）。"""
    good = _write_image(tmp_path / "pages" / "page1.jpg")
    broken = tmp_path / "pages" / "page2.jpg"
    broken.write_bytes(b"not an image")
    stage = _stage(FakeOCRClient())
    await stage.prepare(store, tmp_path / "pages")

    result = await stage.run(store)

    assert (result.succeeded, result.failed) == (1, 1)
    rows = {Path(r.source).name: r for r in await store.read()}
    assert rows["page1.jpg"].ocr_status is StageStatus.DONE
    assert rows["page2.jpg"].ocr_status is StageStatus.FAILED
    assert rows["page2.jpg"].ocr_error != ""
    assert good.exists()


@pytest.mark.asyncio
async def test_service_error_is_recorded_on_the_row(store: CardStore, tmp_path: Path) -> None:
    stage = _stage(FakeOCRClient(error=ExternalServiceError("辨識失敗")))
    await stage.prepare(store, _write_image(tmp_path / "page.jpg"))

    result = await stage.run(store)

    assert result.failed == 1
    assert "辨識失敗" in (await store.read())[0].ocr_error


@pytest.mark.asyncio
async def test_row_without_source_fails_that_row(store: CardStore) -> None:
    await store.write([CardRow(ocr_source_page=1)])

    result = await _stage(FakeOCRClient()).run(store)

    assert result.failed == 1
    assert "沒有影像來源" in (await store.read())[0].ocr_error


@pytest.mark.asyncio
async def test_rerun_skips_completed_rows(store: CardStore, tmp_path: Path) -> None:
    client = FakeOCRClient()
    stage = _stage(client)
    await stage.prepare(store, _write_image(tmp_path / "page.jpg"))
    await stage.run(store)

    result = await stage.run(store)

    assert result.processed == 0
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_only_failed_retries_just_the_failed_row(
    store: CardStore, tmp_path: Path
) -> None:
    pages = tmp_path / "pages"
    _write_image(pages / "page1.jpg")
    (pages / "page2.jpg").write_bytes(b"not an image")
    client = FakeOCRClient()
    stage = _stage(client)
    await stage.prepare(store, pages)
    await stage.run(store)
    client.calls.clear()

    result = await stage.run(store, only_failed=True)

    assert result.processed == 1
    assert result.failed == 1  # 圖還是壞的，但只重試了它


# ── 收尾動作（VRAM 讓渡）────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_finish_runs_after_stage(store: CardStore, tmp_path: Path) -> None:
    calls: list[str] = []

    async def unload() -> bool:
        calls.append("unloaded")
        return True

    stage = _stage(FakeOCRClient(), on_finish=unload)
    await stage.prepare(store, _write_image(tmp_path / "page.jpg"))

    await stage.run(store)

    assert calls == ["unloaded"]


@pytest.mark.asyncio
async def test_no_on_finish_is_fine(store: CardStore, tmp_path: Path) -> None:
    stage = _stage(FakeOCRClient())
    await stage.prepare(store, _write_image(tmp_path / "page.jpg"))

    assert (await stage.run(store)).succeeded == 1


# ── 型別 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_row_returns_no_new_rows(store: CardStore, tmp_path: Path) -> None:
    """OCR 是一對一：一張圖一列，不像 extract 會展開成多列。"""
    stage = _stage(FakeOCRClient())
    await stage.prepare(store, _write_image(tmp_path / "page.jpg"))
    row = (await store.read())[0]

    created: Sequence[CardRow] = await stage.process_row(row)

    assert list(created) == []


# ── vision_direct ───────────────────────────────────────────────


def _vision_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("YAML_SETTINGS_FILE", "agents.yaml")
    monkeypatch.setenv("INGEST_MODE", "vision_direct")
    from anki_deck_builder.config import load_settings

    return load_settings(env_file=None)


@pytest.mark.asyncio
async def test_vision_direct_creates_rows_but_does_not_recognize(
    store: CardStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _stage(ExplodingOCRClient(), settings=_vision_settings(monkeypatch))
    await stage.prepare(store, _write_image(tmp_path / "page.jpg"))

    result = await stage.run(store)

    assert result.processed == 0
    row = (await store.read())[0]
    assert row.source != ""
    assert row.ocr_source_page == 1


@pytest.mark.asyncio
async def test_vision_direct_leaves_raw_text_and_status_untouched(
    store: CardStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空的 raw_text 就是「這列走影像路徑」的標記，狀態不能被動過。"""
    stage = _stage(ExplodingOCRClient(), settings=_vision_settings(monkeypatch))
    await stage.prepare(store, _write_image(tmp_path / "page.jpg"))

    await stage.run(store)

    row = (await store.read())[0]
    assert row.raw_text == ""
    assert row.ocr_status is StageStatus.PENDING


@pytest.mark.asyncio
async def test_vision_direct_skips_unload(
    store: CardStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """沒載入 OCR 模型就沒有 VRAM 要讓。"""
    calls: list[str] = []

    async def unload() -> bool:
        calls.append("unloaded")
        return True

    stage = _stage(
        ExplodingOCRClient(), settings=_vision_settings(monkeypatch), on_finish=unload
    )
    await stage.prepare(store, _write_image(tmp_path / "page.jpg"))

    await stage.run(store)

    assert calls == []


@pytest.mark.asyncio
async def test_text_input_still_bypasses_in_vision_direct(
    store: CardStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """純文字不受輸入模式影響，一律直接讀進 raw_text。"""
    source = tmp_path / "notes.txt"
    source.write_text("あきらめる", encoding="utf-8")
    stage = _stage(ExplodingOCRClient(), settings=_vision_settings(monkeypatch))

    await stage.prepare(store, source)

    assert (await store.read())[0].raw_text == "あきらめる"


@pytest.mark.asyncio
async def test_card_rows_are_not_treated_as_ocr_targets(store: CardStore) -> None:
    """extract 產出的卡片列會沿用來源列的 ocr_status，在 vision_direct 下是
    pending，因而會被選進本階段。它的 source 是書目資訊而非影像路徑。"""
    await store.write(
        [CardRow(card_id="ja_n2_001", front="属する", source="單字書 p.333", ocr_source_page=1)]
    )

    result = await _stage(ExplodingOCRClient()).run(store)

    assert result.failed == 0
    assert (await store.read())[0].ocr_error == ""


@pytest.mark.asyncio
async def test_rows_that_already_have_text_are_not_re_ocred(store: CardStore) -> None:
    """Phase 1 的舊工作檔：raw_text 早有內容、extract 也做完了，但 ocr_status
    一直是 pending（那時沒有 ocr 階段）。這些列沒有 OCR 可做，不該失敗。"""
    await store.write(
        [CardRow(raw_text="□増大\nぞうだい\n[名] 增多，增大", ocr_source_page=1)]
    )

    result = await _stage(ExplodingOCRClient()).run(store)

    assert (result.succeeded, result.failed) == (1, 0)
    row = (await store.read())[0]
    assert row.ocr_status is StageStatus.DONE
    assert row.ocr_error == ""
    assert row.raw_text.startswith("□増大")
