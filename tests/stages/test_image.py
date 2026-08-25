"""image 階段單元測試。

狀態流轉、跳過非卡片列、檔名與相對路徑回填、併發上限等純邏輯**可執行**；
ComfyUI 呼叫一律以假 client 替換（依 Protocol 注入），不觸及任何真實服務。
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from anki_deck_builder.exceptions import ConfigurationError, ExternalServiceError
from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.stages import get_stage
from anki_deck_builder.stages.image import ImageStage, stable_seed
from anki_deck_builder.state import CardStore

PNG = b"\x89PNG\r\n\x1a\n fake"


class FakeImageClient:
    """記錄每次呼叫，可指定回應或錯誤。"""

    def __init__(self, content: bytes = PNG, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[tuple[str, int | None]] = []
        self.validated = 0
        self.in_flight = 0
        self.peak_in_flight = 0

    def validate(self) -> None:
        self.validated += 1

    async def generate(self, positive_prompt: str, seed: int | None = None) -> bytes:
        self.calls.append((positive_prompt, seed))
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            if self.error is not None:
                raise self.error
            return self.content
        finally:
            self.in_flight -= 1


class RejectingClient:
    """`validate()` 就失敗——用於驗證「生成開始前擋下」。"""

    def validate(self) -> None:
        raise ConfigurationError("注入點「正向 prompt」（COMFYUI_POSITIVE_NODE_ID=99）對不上")

    async def generate(self, positive_prompt: str, seed: int | None = None) -> bytes:
        raise AssertionError("設定錯誤時不該送出任何生成請求")  # pragma: no cover


def card(card_id: str = "ja_001", prompt: str = "a tiger among cats", **kwargs) -> CardRow:
    return CardRow(card_id=card_id, front=card_id, back="釋義", image_prompt=prompt, **kwargs)


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "work" / "cards.csv")


async def run_with(store: CardStore, rows: list[CardRow], client, **kwargs) -> tuple:
    await store.write(rows)
    stage = ImageStage(client, **kwargs)
    result = await stage.run(store)
    return result, await store.read()


# ── 註冊 ─────────────────────────────────────────────────────────


def test_registered_under_image() -> None:
    assert get_stage("image") is ImageStage


# ── seed ─────────────────────────────────────────────────────────


def test_seed_is_stable_per_card_id() -> None:
    assert stable_seed("ja_n2_001") == stable_seed("ja_n2_001")


def test_seed_differs_between_cards() -> None:
    assert stable_seed("ja_n2_001") != stable_seed("ja_n2_002")


def test_seed_fits_every_seed_node_range() -> None:
    """seed 節點的上限因節點而異——`Seed (rgthree)` 只到 2^50，超出會讓整份
    workflow 被 ComfyUI 以 `value_bigger_than_max` 退回（換 SDXL workflow 時實測踩到）。"""
    for card_id in ("a", "ja_n2_001", "藥理學_017", "x" * 200):
        assert 0 <= stable_seed(card_id) < 2**31


# ── 正常流程 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generates_and_fills_relative_path(store: CardStore, tmp_path: Path) -> None:
    client = FakeImageClient()

    result, rows = await run_with(store, [card()], client)

    assert result.succeeded == 1
    assert rows[0].image_front == "media/img/ja_001.png"
    assert rows[0].image_status is StageStatus.DONE
    assert (tmp_path / "work" / "media" / "img" / "ja_001.png").read_bytes() == PNG


@pytest.mark.asyncio
async def test_media_root_follows_the_csv_not_the_work_dir(tmp_path: Path) -> None:
    """`pack` 以中間 CSV 所在目錄為 media_root，本階段必須落在同一處。"""
    store = CardStore(tmp_path / "別處" / "cards.csv")
    client = FakeImageClient()

    await run_with(store, [card()], client)

    assert (tmp_path / "別處" / "media" / "img" / "ja_001.png").is_file()


@pytest.mark.asyncio
async def test_prompt_and_seed_passed_to_client(store: CardStore) -> None:
    client = FakeImageClient()

    await run_with(store, [card(prompt="  a tiger among cats  ")], client)

    assert client.calls == [("a tiger among cats", stable_seed("ja_001"))]


@pytest.mark.asyncio
async def test_validate_runs_once_before_generating(store: CardStore) -> None:
    client = FakeImageClient()

    await run_with(store, [card("a"), card("b")], client)

    assert client.validated == 1
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_configuration_error_stops_before_any_request(store: CardStore) -> None:
    """設定錯誤要在第一張送出前拋出，而不是逐列記成 failed。"""
    await store.write([card()])

    with pytest.raises(ConfigurationError, match="COMFYUI_POSITIVE_NODE_ID=99"):
        await ImageStage(RejectingClient()).run(store)

    assert (await store.read())[0].image_status is StageStatus.PENDING


# ── 跳過與失敗 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_source_rows_are_skipped(store: CardStore, tmp_path: Path) -> None:
    """來源列沒有 card_id，image_status 卻是 pending，會被選進本階段。"""
    source_row = CardRow(source="page_01.jpg", raw_text="……", ocr_source_page=1)

    result, rows = await run_with(store, [source_row, card()], FakeImageClient())

    assert result.succeeded == 2
    assert rows[0].image_front == ""
    assert rows[1].image_front == "media/img/ja_001.png"
    assert list((tmp_path / "work" / "media" / "img").iterdir()) == [
        tmp_path / "work" / "media" / "img" / "ja_001.png"
    ]


@pytest.mark.asyncio
async def test_missing_prompt_is_recorded_as_failed(store: CardStore) -> None:
    result, rows = await run_with(store, [card(prompt="")], FakeImageClient())

    assert result.failed == 1
    assert rows[0].image_status is StageStatus.FAILED
    assert "image_prompt" in rows[0].image_error
    assert rows[0].image_front == ""


@pytest.mark.asyncio
async def test_generation_failure_does_not_stop_the_batch(store: CardStore) -> None:
    """單張失敗只影響該列，整批繼續（約束 3）。"""
    calls: list[str] = []

    class FlakyClient(FakeImageClient):
        async def generate(self, positive_prompt: str, seed: int | None = None) -> bytes:
            calls.append(positive_prompt)
            if positive_prompt == "壞的":
                raise ExternalServiceError("ComfyUI 執行失敗（節點 3）：CUDA out of memory")
            return PNG

    rows_in = [card("a"), card("b", prompt="壞的"), card("c")]
    result, rows = await run_with(store, rows_in, FlakyClient())

    assert (result.succeeded, result.failed) == (2, 1)
    assert len(calls) == 3
    assert rows[1].image_status is StageStatus.FAILED
    assert "CUDA out of memory" in rows[1].image_error
    assert [rows[0].image_status, rows[2].image_status] == [StageStatus.DONE] * 2


# ── 選列 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_done_rows_are_left_alone(store: CardStore) -> None:
    """改某列 prompt 並把 image_status 設回 pending 後重跑，只處理該列。"""
    done = card("a", image_status=StageStatus.DONE, image_front="media/img/a.png")
    pending = card("b", prompt="重畫這張")

    result, rows = await run_with(store, [done, pending], client := FakeImageClient())

    assert result.processed == 1
    assert [prompt for prompt, _ in client.calls] == ["重畫這張"]
    assert rows[0].image_front == "media/img/a.png"


@pytest.mark.asyncio
async def test_only_failed_selects_failed_rows(store: CardStore) -> None:
    rows_in = [
        card("a", image_status=StageStatus.DONE),
        card("b", image_status=StageStatus.FAILED, image_error="上次逾時"),
        card("c"),
    ]
    await store.write(rows_in)
    client = FakeImageClient()

    result = await ImageStage(client).run(store, only_failed=True)

    assert result.processed == 1
    assert [prompt for prompt, _ in client.calls] == ["a tiger among cats"]
    assert (await store.read())[1].image_status is StageStatus.DONE


# ── 併發 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrency_defaults_to_one_without_settings() -> None:
    assert ImageStage(FakeImageClient()).concurrency == 1


@pytest.mark.asyncio
async def test_concurrency_follows_batch_size(store: CardStore, tmp_path: Path) -> None:
    """COMFYUI_BATCH_SIZE 是「同時在飛的 prompt 數」，直接對應骨架的 semaphore。"""
    from anki_deck_builder.config import ComfyUISettings, PathSettings, Settings

    settings = Settings.model_construct(
        comfyui=ComfyUISettings.model_construct(batch_size=3),
        paths=PathSettings.model_construct(work_dir=tmp_path),
    )
    client = FakeImageClient()

    await store.write([card(f"c{i}") for i in range(6)])
    stage = ImageStage(client, settings=settings)
    assert stage.concurrency == 3
    await stage.run(store)

    assert client.peak_in_flight == 3


@pytest.mark.asyncio
async def test_checkpoints_every_row(store: CardStore) -> None:
    """單張數十秒，中斷後不該重生已完成的圖。"""
    assert ImageStage(FakeImageClient()).checkpoint_every == 1


# ── 進度顯示 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_progress_counts_every_row(store: CardStore) -> None:
    """非 TTY 降級輸出，最後一項一定留下紀錄。"""
    stream = io.StringIO()

    await run_with(
        store, [card("a"), card("b")], FakeImageClient(), progress_stream=stream
    )

    assert stream.getvalue().splitlines()[-1].startswith("生成聯想圖  2/2  ")


@pytest.mark.asyncio
async def test_progress_advances_on_failure(store: CardStore) -> None:
    """失敗的列也已經處理完了，不前進會讓進度永遠差幾項。"""
    stream = io.StringIO()
    rows = [card("a", prompt=""), card("b")]

    result, _ = await run_with(store, rows, FakeImageClient(), progress_stream=stream)

    assert (result.succeeded, result.failed) == (1, 1)
    assert stream.getvalue().splitlines()[-1].startswith("生成聯想圖  2/2  ")


@pytest.mark.asyncio
async def test_progress_total_counts_only_pending_rows(store: CardStore) -> None:
    """總數要跟骨架實際處理的列數一致，否則進度永遠到不了 100%。"""
    stream = io.StringIO()
    rows = [
        card("a", image_status=StageStatus.DONE),
        card("b"),
        card("c"),
    ]

    await run_with(store, rows, FakeImageClient(), progress_stream=stream)

    assert stream.getvalue().splitlines()[-1].startswith("生成聯想圖  2/2  ")


@pytest.mark.asyncio
async def test_no_progress_output_when_nothing_pending(store: CardStore) -> None:
    stream = io.StringIO()
    rows = [card("a", image_status=StageStatus.DONE)]

    result, _ = await run_with(store, rows, FakeImageClient(), progress_stream=stream)

    assert result.processed == 0
    assert stream.getvalue() == ""
