"""階段共用骨架單元測試（非付費模組，AI 執行至通過）。

以假階段驗證失敗跳過、狀態更新、registry 查表、一對多與 checkpoint。
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.stages import (
    BaseStage,
    get_stage,
    register_stage,
    registered_stages,
)
from anki_deck_builder.stages import base as base_module
from anki_deck_builder.state import CardStore


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """registry 是模組層級的字典，測試之間必須互相隔離。"""
    monkeypatch.setattr(base_module, "_REGISTRY", {})


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "cards.csv")


class RecordingStage(BaseStage):
    """記錄看過哪些列的假階段。"""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def process_row(self, row: CardRow) -> Sequence[CardRow]:
        self.seen.append(row.card_id)
        row.front = f"processed-{row.card_id}"
        return ()


# ── registry ─────────────────────────────────────────────────────


def test_register_and_lookup() -> None:
    @register_stage("extract")
    class Stage(RecordingStage):
        pass

    assert get_stage("extract") is Stage
    assert Stage.name == "extract"


def test_unknown_stage_lists_available() -> None:
    @register_stage("extract")
    class Stage(RecordingStage):
        pass

    with pytest.raises(KeyError) as excinfo:
        get_stage("image")

    assert "extract" in str(excinfo.value)


def test_lookup_with_empty_registry() -> None:
    with pytest.raises(KeyError, match="尚未實作"):
        get_stage("extract")


def test_duplicate_registration_is_rejected() -> None:
    @register_stage("extract")
    class First(RecordingStage):
        pass

    with pytest.raises(ValueError, match="已由 First 註冊"):

        @register_stage("extract")
        class Second(RecordingStage):
            pass


def test_name_must_be_a_known_stage() -> None:
    """`audio` 不是合法階段名——audio_front 與 audio_back 狀態獨立。"""
    with pytest.raises(KeyError) as excinfo:

        @register_stage("audio")
        class Stage(RecordingStage):
            pass

    assert "audio_front" in str(excinfo.value)


def test_registered_stages_returns_a_copy() -> None:
    @register_stage("extract")
    class Stage(RecordingStage):
        pass

    snapshot = registered_stages()
    snapshot.clear()

    assert get_stage("extract") is Stage


# ── 正常流程 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_processes_pending_rows_and_persists(store: CardStore) -> None:
    await store.write([CardRow(card_id="a"), CardRow(card_id="b")])

    @register_stage("extract")
    class Stage(RecordingStage):
        pass

    stage = Stage()
    result = await stage.run(store)

    assert stage.seen == ["a", "b"]
    assert (result.processed, result.succeeded, result.failed) == (2, 2, 0)
    assert result.all_succeeded

    rows = await store.read()
    assert [r.front for r in rows] == ["processed-a", "processed-b"]
    assert all(r.extract_status is StageStatus.DONE for r in rows)


@pytest.mark.asyncio
async def test_skips_done_rows(store: CardStore) -> None:
    await store.write(
        [
            CardRow(card_id="done", extract_status=StageStatus.DONE),
            CardRow(card_id="todo"),
        ]
    )

    @register_stage("extract")
    class Stage(RecordingStage):
        pass

    stage = Stage()
    result = await stage.run(store)

    assert stage.seen == ["todo"]
    assert result.processed == 1


@pytest.mark.asyncio
async def test_no_targets_leaves_file_untouched(store: CardStore) -> None:
    await store.write([CardRow(card_id="done", extract_status=StageStatus.DONE)])
    before = store.path.read_bytes()

    @register_stage("extract")
    class Stage(RecordingStage):
        pass

    result = await Stage().run(store)

    assert result.processed == 0
    assert store.path.read_bytes() == before


@pytest.mark.asyncio
async def test_flags_are_passed_through(store: CardStore) -> None:
    await store.write(
        [
            CardRow(card_id="done", extract_status=StageStatus.DONE),
            CardRow(card_id="failed", extract_status=StageStatus.FAILED),
            CardRow(card_id="pending"),
        ]
    )

    @register_stage("extract")
    class Stage(RecordingStage):
        pass

    only_failed = Stage()
    await only_failed.run(store, only_failed=True)
    assert only_failed.seen == ["failed"]

    forced = Stage()
    await forced.run(store, force=True)
    assert forced.seen == ["done", "failed", "pending"]


@pytest.mark.asyncio
async def test_force_and_only_failed_are_mutually_exclusive(store: CardStore) -> None:
    await store.create_empty()

    @register_stage("extract")
    class Stage(RecordingStage):
        pass

    with pytest.raises(ValueError, match="互斥"):
        await Stage().run(store, force=True, only_failed=True)


# ── 失敗跳過（約束 3）─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_failure_does_not_stop_the_batch(store: CardStore) -> None:
    await store.write([CardRow(card_id=cid) for cid in ("a", "boom", "c")])

    @register_stage("extract")
    class Stage(BaseStage):
        concurrency = 1

        async def process_row(self, row: CardRow) -> Sequence[CardRow]:
            if row.card_id == "boom":
                raise ValueError("模型回傳格式錯誤")
            return ()

    result = await Stage().run(store)

    assert (result.succeeded, result.failed) == (2, 1)
    rows = {r.card_id: r for r in await store.read()}
    assert rows["a"].extract_status is StageStatus.DONE
    assert rows["c"].extract_status is StageStatus.DONE
    assert rows["boom"].extract_status is StageStatus.FAILED
    assert rows["boom"].extract_error == "ValueError: 模型回傳格式錯誤"


@pytest.mark.asyncio
async def test_error_message_is_single_line_and_bounded(store: CardStore) -> None:
    await store.write([CardRow(card_id="a"), CardRow(card_id="b")])

    @register_stage("extract")
    class Stage(BaseStage):
        async def process_row(self, row: CardRow) -> Sequence[CardRow]:
            if row.card_id == "a":
                raise RuntimeError("第一行\n第二行")
            raise RuntimeError("x" * 2000)

    await Stage().run(store)

    rows = {r.card_id: r for r in await store.read()}
    assert rows["a"].extract_error == "RuntimeError: 第一行\\n第二行"
    assert "\n" not in rows["b"].extract_error
    assert len(rows["b"].extract_error) == base_module.MAX_ERROR_LENGTH


@pytest.mark.asyncio
async def test_rerun_clears_previous_error(store: CardStore) -> None:
    await store.write(
        [
            CardRow(
                card_id="a",
                extract_status=StageStatus.FAILED,
                extract_error="上次的錯誤",
            )
        ]
    )

    @register_stage("extract")
    class Stage(RecordingStage):
        pass

    await Stage().run(store)

    row = (await store.read())[0]
    assert row.extract_status is StageStatus.DONE
    assert row.extract_error == ""


# ── 一對多 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_rows_are_appended_and_source_row_kept(store: CardStore) -> None:
    await store.write([CardRow(raw_text="page-1"), CardRow(raw_text="page-2")])

    @register_stage("extract")
    class Stage(BaseStage):
        concurrency = 1

        async def process_row(self, row: CardRow) -> Sequence[CardRow]:
            return [
                CardRow(card_id=f"{row.raw_text}-{i}", front=f"詞條{i}") for i in (1, 2)
            ]

    result = await Stage().run(store)

    assert result.added == 4
    rows = await store.read()
    assert len(rows) == 6
    # 原 raw_text 列保留、標為 done，且 card_id 為空（pack 據此略過）
    source_rows = [r for r in rows if not r.card_id]
    assert len(source_rows) == 2
    assert all(r.extract_status is StageStatus.DONE for r in source_rows)
    assert all(r.raw_text for r in source_rows)
    # 新列狀態全新，下游階段會自然撈到
    new_rows = [r for r in rows if r.card_id]
    assert [r.card_id for r in new_rows] == [
        "page-1-1",
        "page-1-2",
        "page-2-1",
        "page-2-2",
    ]
    assert all(r.image_status is StageStatus.PENDING for r in new_rows)


@pytest.mark.asyncio
async def test_failed_row_adds_nothing(store: CardStore) -> None:
    await store.write([CardRow(raw_text="page-1")])

    @register_stage("extract")
    class Stage(BaseStage):
        async def process_row(self, row: CardRow) -> Sequence[CardRow]:
            raise ValueError("boom")

    result = await Stage().run(store)

    assert result.added == 0
    assert len(await store.read()) == 1


# ── 併發與 checkpoint ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrency_limit_is_respected(store: CardStore) -> None:
    await store.write([CardRow(card_id=f"c{i}") for i in range(10)])

    @register_stage("extract")
    class Stage(BaseStage):
        concurrency = 3

        def __init__(self) -> None:
            self.active = 0
            self.peak = 0

        async def process_row(self, row: CardRow) -> Sequence[CardRow]:
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return ()

    stage = Stage()
    await stage.run(store)

    assert stage.peak <= 3
    assert stage.peak > 1


@pytest.mark.asyncio
async def test_checkpoint_persists_progress_mid_run(store: CardStore) -> None:
    """跑到一半就該看得到已完成的列，否則中斷等於白做。"""
    await store.write([CardRow(card_id=f"c{i}") for i in range(4)])
    seen_mid_run: list[int] = []

    @register_stage("extract")
    class Stage(BaseStage):
        concurrency = 1
        checkpoint_every = 1

        async def process_row(self, row: CardRow) -> Sequence[CardRow]:
            rows = await store.read()
            seen_mid_run.append(sum(1 for r in rows if r.extract_status is StageStatus.DONE))
            return ()

    await Stage().run(store)

    assert seen_mid_run == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_interruption_persists_completed_rows(store: CardStore) -> None:
    """模擬 Ctrl-C：已完成的列必須留在檔案裡，重跑時被跳過。

    Ctrl-C 在 async 程式中會以 `CancelledError` 出現在任務內部。它是 `BaseException`，
    刻意不被「失敗跳過」的 `except Exception` 攔下——中斷不是單列失敗，
    整批該停，但已完成的進度必須寫回。
    """
    await store.write([CardRow(card_id=f"c{i}") for i in range(3)])

    @register_stage("extract")
    class Stage(BaseStage):
        concurrency = 1
        checkpoint_every = 0  # 只靠離開前那次 flush

        async def process_row(self, row: CardRow) -> Sequence[CardRow]:
            if row.card_id == "c2":
                raise asyncio.CancelledError
            return ()

    with pytest.raises(asyncio.CancelledError):
        await Stage().run(store)

    rows = await store.read()
    statuses = {r.card_id: r.extract_status for r in rows}
    assert statuses["c0"] is StageStatus.DONE
    assert statuses["c1"] is StageStatus.DONE
    assert statuses["c2"] is StageStatus.PENDING


# ── 階段性驗證 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_settings_runs_before_reading(tmp_path: Path) -> None:
    """設定不對時要在讀檔前就停，不要留下半吊子的狀態。"""
    missing = CardStore(tmp_path / "nope.csv")

    @register_stage("image")
    class Stage(BaseStage):
        async def process_row(self, row: CardRow) -> Sequence[CardRow]:
            return ()

        def validate_settings(self) -> None:
            raise ValueError("COMFYUI_WORKFLOW_PATH 指向的檔案不存在")

    with pytest.raises(ValueError, match="COMFYUI_WORKFLOW_PATH"):
        await Stage().run(missing)
