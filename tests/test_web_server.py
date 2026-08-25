"""FastAPI 端點測試。

以 `httpx.ASGITransport` 直接打 app，**不起真的 server、不綁埠**——httpx 本來
就是專案相依，不必為了測試多裝東西。

階段一律以假物件替換：真的跑會載模型、佔 VRAM、打外部服務。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from anki_deck_builder.config import Settings, load_settings
from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.state import CardStore
from anki_deck_builder.web import service
from anki_deck_builder.web.server import create_app


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


@pytest.fixture
def work(tmp_path: Path) -> Path:
    path = tmp_path / "work" / "cards.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def client(settings: Settings, work: Path):  # noqa: ANN201 - httpx.AsyncClient
    app = create_app(settings=settings, work=work, with_ui=False)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test"), app


def _card(card_id: str = "a", **overrides: Any) -> CardRow:
    values: dict[str, Any] = {
        "card_id": card_id,
        "deck": "日語::N2",
        "front": "属する",
        "image_prompt": "a tiger among cats",
        "extract_status": StageStatus.DONE,
        "image_status": StageStatus.DONE,
    }
    values.update(overrides)
    return CardRow(**values)


async def _seed(work: Path, *rows: CardRow) -> None:
    await CardStore(work).write(list(rows))


class FakeStage:
    def __init__(self, name: str = "image", block: asyncio.Event | None = None) -> None:
        self.name = name
        self.block = block
        self.calls: list[dict[str, Any]] = []

    async def run(self, store: CardStore, **kwargs: Any) -> Any:
        from anki_deck_builder.stages.base import StageResult

        self.calls.append(kwargs)
        if self.block is not None:
            await self.block.wait()
        return StageResult(stage=self.name, processed=2, succeeded=2, failed=0, added=0)


def _patch_stage(monkeypatch: pytest.MonkeyPatch, *stages: FakeStage) -> None:
    """替換掉 `_prepare`，讓端點測試不碰真的階段組裝與 VRAM 讓渡。"""

    async def prepare(settings: Settings, stage: str) -> list[FakeStage]:
        return list(stages)

    monkeypatch.setattr(service, "_prepare", prepare)


# ── 讀取端點 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_endpoint_counts_stages(client, work: Path) -> None:
    http, _ = client
    await _seed(work, _card("a"), _card("b", image_status=StageStatus.FAILED))

    async with http:
        body = (await http.get("/api/status")).json()

    assert body["image"] == {"pending": 0, "done": 1, "failed": 1}


@pytest.mark.asyncio
async def test_rows_endpoint_paginates(client, work: Path) -> None:
    http, _ = client
    await _seed(work, *(_card(f"c{i}") for i in range(5)))

    async with http:
        body = (await http.get("/api/rows", params={"offset": 1, "limit": 2})).json()

    assert body["total"] == 5
    assert [row["card_id"] for row in body["rows"]] == ["c1", "c2"]


@pytest.mark.asyncio
async def test_rows_endpoint_rejects_bad_paging(client, work: Path) -> None:
    http, _ = client
    await _seed(work, _card())

    async with http:
        assert (await http.get("/api/rows", params={"offset": -1})).status_code == 422
        assert (await http.get("/api/rows", params={"limit": 0})).status_code == 422


@pytest.mark.asyncio
async def test_status_filter_without_stage_is_a_client_error(client, work: Path) -> None:
    http, _ = client
    await _seed(work, _card())

    async with http:
        response = await http.get("/api/rows", params={"status": "failed"})

    assert response.status_code == 404
    assert "stage" in response.json()["detail"]


@pytest.mark.asyncio
async def test_failed_endpoint_lists_reasons(client, work: Path) -> None:
    http, _ = client
    await _seed(
        work, _card("a", image_status=StageStatus.FAILED, image_error="沒有 prompt")
    )

    async with http:
        body = (await http.get("/api/failed")).json()

    assert body["failed"] == [
        {"card_id": "a", "stage": "image", "error": "沒有 prompt", "front": "属する"}
    ]


@pytest.mark.asyncio
async def test_config_endpoint_masks_the_key(client) -> None:
    http, _ = client

    async with http:
        body = (await http.get("/api/config")).json()

    assert "****" in body["agent_factory"]["openai_api_key"]
    assert "sk-test-key-value" not in str(body)


# ── 媒體端點 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_image_endpoint_serves_the_registered_file(client, work: Path) -> None:
    http, _ = client
    image = work.parent / "media" / "img" / "a.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    await _seed(work, _card("a", image_front="media/img/a.png"))

    async with http:
        response = await http.get("/api/media/img/a")

    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_image_endpoint_refuses_path_traversal(client, work: Path) -> None:
    """CSV 的欄位是人可以編輯的，不能變成讀任意檔案的管道。"""
    http, _ = client
    (work.parent.parent / "secret.txt").write_text("內容")
    await _seed(work, _card("a", image_front="../secret.txt"))

    async with http:
        response = await http.get("/api/media/img/a")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_image_endpoint_rejects_unknown_side(client, work: Path) -> None:
    http, _ = client
    await _seed(work, _card("a"))

    async with http:
        response = await http.get("/api/media/img/a", params={"side": "middle"})

    assert response.status_code == 422


# ── 編輯端點 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_row_cascades_status(client, work: Path) -> None:
    http, _ = client
    await _seed(work, _card("a"))

    async with http:
        body = (
            await http.patch("/api/rows/a", json={"image_prompt": "a lone wolf"})
        ).json()

    assert body["image_status"] == "pending"
    assert body["extract_status"] == "pending"


@pytest.mark.asyncio
async def test_patch_row_rejects_readonly_fields(client, work: Path) -> None:
    http, _ = client
    await _seed(work, _card("a"))

    async with http:
        response = await http.patch("/api/rows/a", json={"image_status": "done"})

    assert response.status_code == 404
    assert "image_status" in response.json()["detail"]


@pytest.mark.asyncio
async def test_patch_unknown_card_is_404(client, work: Path) -> None:
    http, _ = client
    await _seed(work, _card("a"))

    async with http:
        response = await http.patch("/api/rows/zzz", json={"front": "x"})

    assert response.status_code == 404


# ── 執行與進度 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_endpoint_returns_immediately_and_reports_progress(
    client, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """階段可能跑半小時，請求端只拿「已受理」，結果另外查。"""
    http, app = client
    await _seed(work, _card("a"))
    stage = FakeStage("image")
    _patch_stage(monkeypatch, stage)

    async with http:
        accepted = await http.post("/api/stages/image/run")
        await app.state.runner.wait()
        progress = (await http.get("/api/stages/image/progress")).json()

    assert accepted.status_code == 200 and accepted.json()["accepted"] is True
    assert progress["running"] is False
    assert progress["results"] == [
        {
            "stage": "image",
            "processed": 2,
            "succeeded": 2,
            "failed": 0,
            "added": 0,
        }
    ]


@pytest.mark.asyncio
async def test_run_endpoint_passes_card_ids(
    client, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「只重生這一張圖」靠的是骨架的 `card_ids` 擴充，不是 web 自己篩。"""
    http, app = client
    await _seed(work, _card("a"), _card("b"))
    stage = FakeStage("image")
    _patch_stage(monkeypatch, stage)

    async with http:
        await http.post("/api/stages/image/run", json={"card_ids": ["b"]})
        await app.state.runner.wait()

    assert stage.calls == [{"force": False, "only_failed": False, "card_ids": ["b"]}]


@pytest.mark.asyncio
async def test_second_run_is_refused_while_one_is_running(
    client, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GPU 只有一張，兩個階段並行必然搶 VRAM，還會互相覆蓋 checkpoint。"""
    http, app = client
    await _seed(work, _card("a"))
    gate = asyncio.Event()
    _patch_stage(monkeypatch, FakeStage("image", block=gate))

    async with http:
        first = await http.post("/api/stages/image/run")
        second = await http.post("/api/stages/audio/run")
        gate.set()
        await app.state.runner.wait()

    assert first.status_code == 200
    assert second.status_code == 409
    assert "image" in second.json()["detail"]


@pytest.mark.asyncio
async def test_run_is_allowed_again_after_the_first_finishes(
    client, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, app = client
    await _seed(work, _card("a"))
    _patch_stage(monkeypatch, FakeStage("image"))

    async with http:
        await http.post("/api/stages/image/run")
        await app.state.runner.wait()
        again = await http.post("/api/stages/image/run")
        await app.state.runner.wait()

    assert again.status_code == 200


@pytest.mark.asyncio
async def test_stage_failure_is_reported_in_progress(
    client, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """背景任務的例外沒有人接，必須留在進度上，否則 UI 只會看到「跑完了」。"""
    http, app = client
    await _seed(work, _card("a"))

    async def prepare(settings: Settings, stage: str) -> list[Any]:
        raise RuntimeError("ComfyUI 沒開")

    monkeypatch.setattr(service, "_prepare", prepare)

    async with http:
        await http.post("/api/stages/image/run")
        await app.state.runner.wait()
        progress = (await http.get("/api/stages/image/progress")).json()

    assert progress["running"] is False
    assert "ComfyUI 沒開" in progress["error"]


@pytest.mark.asyncio
async def test_audio_alias_runs_both_sides(
    client, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, app = client
    await _seed(work, _card("a"))
    _patch_stage(monkeypatch, FakeStage("audio_front"), FakeStage("audio_back"))

    async with http:
        await http.post("/api/stages/audio/run")
        await app.state.runner.wait()
        progress = (await http.get("/api/stages/audio/progress")).json()

    assert [r["stage"] for r in progress["results"]] == ["audio_front", "audio_back"]


@pytest.mark.asyncio
async def test_unknown_stage_is_404(client) -> None:
    http, _ = client

    async with http:
        assert (await http.post("/api/stages/pack/run")).status_code == 404
        assert (await http.get("/api/stages/pack/progress")).status_code == 404


@pytest.mark.asyncio
async def test_force_and_only_failed_are_mutually_exclusive(client, work: Path) -> None:
    """與 CLI 同一條規則（`select_pending` 也會擋），介面層先擋下省一次背景任務。"""
    http, _ = client
    await _seed(work, _card("a"))

    async with http:
        response = await http.post(
            "/api/stages/image/run", params={"force": True, "only_failed": True}
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_progress_before_any_run_is_idle(client) -> None:
    http, _ = client

    async with http:
        body = (await http.get("/api/stages/image/progress")).json()

    assert body == {
        "stage": "image",
        "running": False,
        "elapsed": None,
        "error": None,
        "results": [],
        "counts": {"pending": 0, "done": 0, "failed": 0},
    }


# ── Gradio 掛載 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ui_mounts_without_shadowing_the_api(settings: Settings, work: Path) -> None:
    """UI 掛在根路徑，`/api/*` 必須照常——兩者共用同一個行程與 runner。"""
    await _seed(work, _card("a"))
    app = create_app(settings=settings, work=work)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        page = await http.get("/")
        api = await http.get("/api/status")

    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert api.json()["image"]["done"] == 1
