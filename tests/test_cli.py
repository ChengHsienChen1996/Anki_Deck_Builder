"""CLI 單元測試（非付費模組，AI 執行至通過）。

`extract` 的 LLM 呼叫以假 client 取代，測試不觸及任何模型。
"""

import zipfile
from pathlib import Path

import pytest

from anki_deck_builder.cli import build_parser, main
from anki_deck_builder.exceptions import ConfigurationError, ExternalServiceError
from anki_deck_builder.schemas import CardRow, ExtractedCard, ExtractOutput, StageStatus
from anki_deck_builder.stages.image import stable_seed
from anki_deck_builder.state import CardStore

from .conftest import clear_project_env

SUBCOMMANDS = (
    "ocr",
    "extract",
    "image",
    "audio",
    "pack",
    "run-all",
    "status",
    "serve",
    "reset",
)


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    """把每個測試關進空目錄與乾淨的環境變數。

    **autouse 是刻意的**：`load_settings()` 預設會讀相對路徑的 `.env`，少掛這個
    fixture 的測試會在專案根目錄以開發者的真實設定執行。這個洞被踩過兩次——
    `image` 接上後把圖生成到真實的 `work/`，`audio` 接上後又把音檔寫了進去。
    改成 autouse 之後，忘記掛也不會再有這種事；仍需要顯式參數的測試照樣可以
    要求它（同一個 fixture 實例）。
    """
    monkeypatch.chdir(tmp_path)
    clear_project_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("YAML_SETTINGS_FILE", "agents.yaml")
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.delenv("GLOBAL_CONCURRENCY", raising=False)
    return monkeypatch


def _tiny_png() -> bytes:
    """真的能被 Pillow 解開的 8×8 PNG。

    轉檔上線後假位元組不再夠用：`media_encode.encode_image()` 會真的把它解開
    重編成 WebP，餵假資料等於在測「轉檔會不會失敗」而不是階段邏輯。
    """
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), (120, 80, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _tiny_png()


class FakeComfyUIClient:
    """替換掉 cli 內延後匯入的 ComfyUIClient。"""

    error: Exception | None = None
    calls: list[tuple[str, int | None]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def validate(self) -> None:
        return None

    async def generate(self, positive_prompt: str, seed: int | None = None) -> bytes:
        FakeComfyUIClient.calls.append((positive_prompt, seed))
        if FakeComfyUIClient.error is not None:
            raise FakeComfyUIClient.error
        return PNG


def _tiny_wav() -> bytes:
    """真的能被 soundfile 解開的 0.1 秒靜音 WAV（同理，轉檔會真的解開它）。"""
    from io import BytesIO

    import numpy as np
    import soundfile as sf

    buffer = BytesIO()
    sf.write(buffer, np.zeros(4800, dtype="float32"), 48000, format="WAV")
    return buffer.getvalue()


WAV = _tiny_wav()


class FakeVoxCPMClient:
    """替換掉 cli 內延後匯入的 VoxCPMClient。"""

    error: Exception | None = None
    calls: list[str] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def validate(self) -> None:
        return None

    async def synthesize(self, text: str) -> bytes:
        FakeVoxCPMClient.calls.append(text)
        if FakeVoxCPMClient.error is not None:
            raise FakeVoxCPMClient.error
        return WAV


@pytest.fixture(autouse=True)
def isolate_external_services(monkeypatch: pytest.MonkeyPatch):
    """CLI 測試一律不碰真實的 Ollama 與 ComfyUI。

    這不是潔癖：`image` 接上之前，少了 `env` fixture 的測試會在專案根目錄
    以真實 `.env` 執行，把圖生成到開發者自己的 `work/` 裡。`audio` 接上後
    同一個洞會變成載入 4.58 GB 的語音模型。介面層測試要驗的是「有沒有照設定
    接線」，不是外部服務本身——後者由 client 的測試負責。
    """

    async def no_ensure_room(*args: object, **kwargs: object) -> list[str]:
        return []

    async def no_unload(*args: object, **kwargs: object) -> bool:
        return True

    async def no_free(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(
        "anki_deck_builder.clients.model_unload.ensure_room", no_ensure_room
    )
    monkeypatch.setattr("anki_deck_builder.clients.model_unload.unload_model", no_unload)
    monkeypatch.setattr("anki_deck_builder.clients.comfyui_client.free_memory", no_free)
    monkeypatch.setattr(
        "anki_deck_builder.clients.comfyui_client.ComfyUIClient", FakeComfyUIClient
    )
    monkeypatch.setattr(
        "anki_deck_builder.clients.tts_client.VoxCPMClient", FakeVoxCPMClient
    )
    FakeComfyUIClient.error = None
    FakeComfyUIClient.calls = []
    FakeVoxCPMClient.error = None
    FakeVoxCPMClient.calls = []
    return monkeypatch


@pytest.fixture
def work_csv(tmp_path: Path) -> Path:
    return tmp_path / "work" / "cards.csv"


async def _write(path: Path, rows: list[CardRow]) -> None:
    await CardStore(path).write(rows)


def _write_sync(path: Path, rows: list[CardRow]) -> None:
    import asyncio

    asyncio.run(_write(path, rows))


def _read_sync(path: Path) -> list[CardRow]:
    import asyncio

    return asyncio.run(CardStore(path).read())


class FakeLLMClient:
    """替換掉 cli 內延後匯入的 LLMClient。"""

    responses: list[ExtractOutput] = []
    error: Exception | None = None
    last_input: object = None
    material_verdict: str = "HAS_DEFINITIONS"
    detect_calls: int = 0
    #: `SceneAgent` 的回覆。語義層產出——不含風格詞、觸發詞與後綴
    scene: str = "a tiger standing among a family of housecats, taxonomy mood"
    scene_calls: int = 0
    #: 語法層的回覆。含觸發詞，且內容詞與 `scene` 重疊（要過語義保底檢查）
    prompt: str = (
        "Anime. A tiger stands among a family of housecats, taxonomy mood. "
        "Soft cinematic light, muted colors."
    )
    prompt_calls: int = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls = 0

    async def run_agent(self, agent_name: str, input_: object) -> object:
        if agent_name == "MaterialTypeAgent":
            # 教材判斷另計，不消耗預設的回應序列
            FakeLLMClient.detect_calls += 1
            return FakeLLMClient.material_verdict
        if agent_name == "SceneAgent":
            # 場景另計，不消耗預設的回應序列（同 MaterialTypeAgent）
            FakeLLMClient.scene_calls += 1
            if FakeLLMClient.error is not None:
                raise FakeLLMClient.error
            return FakeLLMClient.scene
        if agent_name.startswith("ImagePrompt"):
            # 語法層同上。前綴比對是刻意的——profile 換 agent 時測試不必跟著改
            FakeLLMClient.prompt_calls += 1
            if FakeLLMClient.error is not None:
                raise FakeLLMClient.error
            return FakeLLMClient.prompt
        FakeLLMClient.last_input = input_
        if FakeLLMClient.error is not None:
            raise FakeLLMClient.error
        self.calls += 1
        return FakeLLMClient.responses[self.calls - 1]

    def model_endpoint(self, agent_name: str) -> tuple[str, str]:
        return ("http://localhost:11434/v1/", "gemma4_31b_q4_K_M-optimized")


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch):
    FakeLLMClient.error = None
    FakeLLMClient.material_verdict = "HAS_DEFINITIONS"
    FakeLLMClient.detect_calls = 0
    FakeLLMClient.scene_calls = 0
    FakeLLMClient.scene = "a tiger standing among a family of housecats, taxonomy mood"
    FakeLLMClient.prompt_calls = 0
    FakeLLMClient.prompt = (
        "Anime. A tiger stands among a family of housecats, taxonomy mood. "
        "Soft cinematic light, muted colors."
    )
    FakeLLMClient.responses = [
        ExtractOutput(
            cards=[
                ExtractedCard(
                    card_id="ja_n2_p1_001",
                    deck="日語::N2::動詞",
                    front="属する",
                    back="屬於，歸於",
                    reading="ぞくする",
                    tts_front_text="属する",
                    tts_back_text="虎はネコ科に属する。",
                )
            ]
        )
    ]
    monkeypatch.setattr(
        "anki_deck_builder.clients.llm_client.LLMClient", FakeLLMClient
    )
    return FakeLLMClient


# ── 參數解析 ─────────────────────────────────────────────────────


#: 子命令 → 讓 parser 過關的最小參數。`reset` 必須擇一說明要做什麼，
#: 這是刻意的：沒指定就跑的「重置」很難不誤觸
MINIMAL_ARGS: dict[str, list[str]] = {"reset": ["--stages", "image"]}


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_every_subcommand_is_defined(command: str) -> None:
    args = build_parser().parse_args([command, *MINIMAL_ARGS.get(command, [])])

    assert args.command == command


def test_reset_requires_saying_what_to_reset() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["reset"])

    assert excinfo.value.code == 2


def test_help_exits_cleanly() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--help"])

    assert excinfo.value.code == 0


def test_subcommand_is_required() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args([])

    assert excinfo.value.code == 2


@pytest.mark.parametrize("command", ["ocr", "extract", "image", "audio", "run-all"])
def test_force_and_only_failed_are_rejected_at_parse_time(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args([command, "--force", "--only-failed"])

    assert excinfo.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_extract_accepts_its_parameters() -> None:
    args = build_parser().parse_args(
        [
            "extract",
            "--work", "w.csv",
            "--deck-name", "日語::N2",
            "--domain", "日語 N2 單字",
            "--source", "單字書 p.333",
            "--card-id-prefix", "ja_n2",
            "--only-failed",
        ]
    )

    assert args.deck_name == "日語::N2"
    assert args.card_id_prefix == "ja_n2"
    assert args.only_failed is True
    assert args.force is False


def test_future_subcommands_keep_their_arguments() -> None:
    """後續 phase 只接上實作，不改參數結構。"""
    assert build_parser().parse_args(["ocr", "--input", "pages/"]).input == "pages/"
    assert build_parser().parse_args(["serve", "--port", "8000"]).port == 8000
    assert build_parser().parse_args(["pack", "--allow-failed"]).allow_failed is True


# ── reset ───────────────────────────────────────────────────────


def test_reset_stages_sets_them_pending(env: pytest.MonkeyPatch, work_csv: Path) -> None:
    _write_sync(
        work_csv,
        [
            CardRow(
                card_id="a1",
                image_status=StageStatus.DONE,
                image_front="media/img/a1.webp",
                audio_front_status=StageStatus.DONE,
            )
        ],
    )

    assert main(["reset", "--work", str(work_csv), "--stages", "image"]) == 0

    row = _read_sync(work_csv)[0]
    assert row.image_status is StageStatus.PENDING
    assert row.audio_front_status is StageStatus.DONE
    assert row.image_front == "media/img/a1.webp"  # 內容欄位不動


def test_reset_rejects_unknown_stage(
    env: pytest.MonkeyPatch, work_csv: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`audio` 不是階段名——訊息要指出合法值，不是丟 traceback。"""
    _write_sync(work_csv, [CardRow(card_id="a1")])

    assert main(["reset", "--work", str(work_csv), "--stages", "audio"]) == 1
    assert "audio_front" in capsys.readouterr().err


def test_clear_without_yes_changes_nothing(
    env: pytest.MonkeyPatch, work_csv: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """破壞性操作預設什麼都不做，只說會刪掉什麼。"""
    _write_sync(work_csv, [CardRow(card_id="a1"), CardRow(card_id="a2")])

    code = main(["reset", "--work", str(work_csv), "--clear", "rows"])

    assert code == 1
    assert "--yes" in capsys.readouterr().out
    assert len(_read_sync(work_csv)) == 2


def test_clear_rows_keeps_media(env: pytest.MonkeyPatch, work_csv: Path) -> None:
    _write_sync(work_csv, [CardRow(card_id="a1")])
    image = work_csv.parent / "media" / "img" / "a1.webp"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"webp")

    assert main(["reset", "--work", str(work_csv), "--clear", "rows", "--yes"]) == 0

    assert _read_sync(work_csv) == []
    assert image.is_file()


def test_clear_all_removes_media(env: pytest.MonkeyPatch, work_csv: Path) -> None:
    _write_sync(work_csv, [CardRow(card_id="a1")])
    for name, blob in (("media/img/a1.webp", b"webp"), ("media/audio/a1_front.mp3", b"mp3")):
        path = work_csv.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)

    assert main(["reset", "--work", str(work_csv), "--clear", "all", "--yes"]) == 0

    assert not list((work_csv.parent / "media" / "img").iterdir())
    assert not list((work_csv.parent / "media" / "audio").iterdir())


def test_reset_backs_up_the_work_file(env: pytest.MonkeyPatch, work_csv: Path) -> None:
    _write_sync(work_csv, [CardRow(card_id="a1")])

    main(["reset", "--work", str(work_csv), "--clear", "rows", "--yes"])

    backups = list(work_csv.parent.glob("cards.csv.bak-*"))
    assert len(backups) == 1
    assert "a1" in backups[0].read_text(encoding="utf-8-sig")


# ── serve ───────────────────────────────────────────────────────


def test_serve_passes_host_port_and_work_file(
    env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """服務要開在設定的工作檔上——`serve` 沒有 `--work`（Phase 1 的參數結構不改）。"""
    seen: dict[str, object] = {}

    async def fake_serve(host: str, port: int, work: object) -> None:
        seen.update(host=host, port=port, work=Path(work).name)

    monkeypatch.setattr("anki_deck_builder.web.server.serve", fake_serve)

    code = main(["serve", "--host", "0.0.0.0", "--port", "8080"])

    assert code == 0
    assert seen == {"host": "0.0.0.0", "port": 8080, "work": "cards.csv"}


def test_serve_defaults_to_localhost(
    env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """沒有帳號也沒有權限控管的本機工具，預設不該綁到對外位址。"""
    seen: dict[str, object] = {}

    async def fake_serve(host: str, port: int, work: object) -> None:
        seen.update(host=host, port=port)

    monkeypatch.setattr("anki_deck_builder.web.server.serve", fake_serve)

    main(["serve"])

    assert seen == {"host": "127.0.0.1", "port": 7860}


# ── 設定錯誤 ─────────────────────────────────────────────────────


def test_missing_required_env_names_the_variable(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """驗收流程第 2 步：缺變數時報錯必須指出變數名。"""
    env.delenv("YAML_SETTINGS_FILE")

    code = main(["status"])

    assert code == 1
    assert "YAML_SETTINGS_FILE" in capsys.readouterr().err


def test_missing_work_file_reports_clearly(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = main(["status", "--work", str(tmp_path / "nope.csv")])

    assert code == 1
    assert "不存在" in capsys.readouterr().err


# ── status ───────────────────────────────────────────────────────


def test_status_reports_every_stage(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], work_csv: Path
) -> None:
    rows = [
        CardRow(
            card_id=f"a{i}",
            ocr_status=StageStatus.DONE,
            extract_status=StageStatus.DONE,
        )
        for i in range(3)
    ]
    rows.append(
        CardRow(
            card_id="a34",
            extract_status=StageStatus.FAILED,
            extract_error="card_id duplicated",
        )
    )
    _write_sync(work_csv, rows)

    code = main(["status", "--work", str(work_csv)])

    out = capsys.readouterr().out
    assert code == 0
    assert "共 4 列" in out
    assert "ocr" in out and "extract" in out
    assert "image" in out and "audio_front" in out and "audio_back" in out
    assert "失敗明細（extract）" in out
    assert "a34  card_id duplicated" in out


def test_status_uses_work_dir_by_default(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], work_csv: Path
) -> None:
    _write_sync(work_csv, [CardRow(card_id="a1")])

    code = main(["status"])

    assert code == 0
    assert str(work_csv) in capsys.readouterr().out


def test_status_on_empty_file(
    env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], work_csv: Path
) -> None:
    _write_sync(work_csv, [])

    code = main(["status", "--work", str(work_csv)])

    out = capsys.readouterr().out
    assert code == 0
    assert "共 0 列" in out
    assert "失敗明細" not in out


# ── extract ──────────────────────────────────────────────────────


def test_extract_runs_and_reports(
    env: pytest.MonkeyPatch,
    fake_llm,
    capsys: pytest.CaptureFixture[str],
    work_csv: Path,
) -> None:
    _write_sync(work_csv, [CardRow(raw_text="□属する", ocr_source_page=1)])

    code = main(
        [
            "extract",
            "--work", str(work_csv),
            "--deck-name", "日語::N2",
            "--source", "單字書 p.333",
            "--card-id-prefix", "ja_n2",
        ]
    )

    assert code == 0
    assert "新增 1 張卡" in capsys.readouterr().out
    rows = _read_sync(work_csv)
    assert len(rows) == 2
    assert rows[1].card_id == "ja_n2_p1_001"
    assert rows[1].source == "單字書 p.333"


def test_extract_returns_error_code_when_a_row_fails(
    env: pytest.MonkeyPatch,
    fake_llm,
    capsys: pytest.CaptureFixture[str],
    work_csv: Path,
) -> None:
    fake_llm.error = TimeoutError("read timeout")
    _write_sync(work_csv, [CardRow(raw_text="□属する")])

    code = main(["extract", "--work", str(work_csv)])

    assert code == 1
    assert "status" in capsys.readouterr().err


# ── pack ─────────────────────────────────────────────────────────


def _packable_row(card_id: str = "a1", **overrides: object) -> CardRow:
    data: dict[str, object] = {
        "card_id": card_id,
        "deck": "日語::N2::動詞",
        "card_type": "basic",
        "front": "属する",
        "back": "屬於",
        "extract_status": StageStatus.DONE,
    }
    data.update(overrides)
    return CardRow(**data)


def test_pack_writes_zip(
    env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    work_csv: Path,
    tmp_path: Path,
) -> None:
    _write_sync(work_csv, [_packable_row()])
    output = tmp_path / "deck.zip"

    code = main(["pack", "--work", str(work_csv), "--output", str(output)])

    assert code == 0
    assert output.exists()
    assert "1 張卡" in capsys.readouterr().out
    with zipfile.ZipFile(output) as archive:
        assert "cards.csv" in archive.namelist()


def test_pack_uses_output_dir_by_default(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path
) -> None:
    _write_sync(work_csv, [_packable_row()])

    code = main(["pack", "--work", str(work_csv)])

    assert code == 0
    assert (tmp_path / "output" / "deck.zip").exists()


def test_pack_aborts_on_failed_rows(
    env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    work_csv: Path,
    tmp_path: Path,
) -> None:
    _write_sync(
        work_csv,
        [_packable_row("a1", image_status=StageStatus.FAILED, image_error="timeout")],
    )
    output = tmp_path / "deck.zip"

    code = main(["pack", "--work", str(work_csv), "--output", str(output)])

    assert code == 1
    assert "--allow-failed" in capsys.readouterr().err
    assert not output.exists()


def test_pack_allow_failed(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path
) -> None:
    _write_sync(
        work_csv,
        [_packable_row("a1", image_status=StageStatus.FAILED, image_error="timeout")],
    )
    output = tmp_path / "deck.zip"

    code = main(["pack", "--work", str(work_csv), "--output", str(output), "--allow-failed"])

    assert code == 0
    assert output.exists()


# ── ocr 子命令（Phase 2）─────────────────────────────────────────


class FakeOCRClient:
    """替換掉 cli 內延後匯入的 OCRClient。"""

    text = "□属する\nぞくする\n[自サ] 屬於"
    endpoint = ("http://localhost:11434/v1/", "glm-ocr-optimized:latest")

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls = 0

    async def recognize(self, image_b64: str) -> str:
        self.calls += 1
        return FakeOCRClient.text

    def model_endpoint(self) -> tuple[str, str]:
        return FakeOCRClient.endpoint


@pytest.fixture
def fake_ocr(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("anki_deck_builder.clients.ocr_client.OCRClient", FakeOCRClient)
    return FakeOCRClient


def _make_page(path: Path) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (60, 40), (210, 210, 210)).save(path)
    return path


def test_ocr_fills_raw_text(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, capsys
) -> None:
    page = _make_page(tmp_path / "pages" / "page1.jpg")

    code = main(["ocr", "--input", str(page), "--work", str(work_csv)])

    assert code == 0
    rows = _read_sync(work_csv)
    assert rows[0].raw_text == FakeOCRClient.text
    assert rows[0].ocr_status is StageStatus.DONE
    assert "新增 1 列" in capsys.readouterr().out


def test_ocr_text_input_does_not_call_client(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr
) -> None:
    """純文字必須完全繞過 OCR（phase-2-ocr.md 的完成標準）。"""
    source = tmp_path / "notes.txt"
    source.write_text("あきらめる 放棄；死心", encoding="utf-8")

    assert main(["ocr", "--input", str(source), "--work", str(work_csv)]) == 0
    assert _read_sync(work_csv)[0].raw_text == "あきらめる 放棄；死心"


def test_ocr_without_input_and_without_work_file_errors(
    env: pytest.MonkeyPatch, work_csv: Path, fake_ocr, capsys
) -> None:
    code = main(["ocr", "--work", str(work_csv)])

    assert code == 1
    assert "未指定 --input" in capsys.readouterr().err


def test_ocr_without_input_resumes_existing_rows(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr
) -> None:
    """路徑存在 CSV 裡，重跑不必再給 --input。"""
    page = _make_page(tmp_path / "pages" / "page1.jpg")
    main(["ocr", "--input", str(page), "--work", str(work_csv)])
    _write_sync(work_csv, [CardRow(source=str(page), ocr_source_page=1)])

    assert main(["ocr", "--work", str(work_csv)]) == 0
    assert _read_sync(work_csv)[0].raw_text == FakeOCRClient.text


def test_ocr_reports_failure_with_exit_code(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, capsys
) -> None:
    broken = tmp_path / "pages" / "page1.jpg"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"not an image")

    code = main(["ocr", "--input", str(broken), "--work", str(work_csv)])

    assert code == 1
    assert "status" in capsys.readouterr().err


def test_ocr_vision_direct_creates_rows_only(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, capsys
) -> None:
    env.setenv("INGEST_MODE", "vision_direct")
    page = _make_page(tmp_path / "pages" / "page1.jpg")

    code = main(["ocr", "--input", str(page), "--work", str(work_csv)])

    assert code == 0
    assert "vision_direct" in capsys.readouterr().out
    assert _read_sync(work_csv)[0].raw_text == ""


def test_ocr_unload_is_off_by_default(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, monkeypatch
) -> None:
    calls: list[str] = []

    async def spy(*args: object, **kwargs: object) -> bool:
        calls.append("unload")
        return True

    monkeypatch.setattr("anki_deck_builder.clients.model_unload.unload_model", spy)
    main(["ocr", "--input", str(_make_page(tmp_path / "p.jpg")), "--work", str(work_csv)])

    assert calls == []


def test_ocr_unload_runs_when_enabled(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, monkeypatch
) -> None:
    env.setenv("MODEL_UNLOAD_ENABLED", "true")
    seen: list[tuple[str, str]] = []

    async def spy(base_url: str, model: str, **kwargs: object) -> bool:
        seen.append((base_url, model))
        return True

    monkeypatch.setattr("anki_deck_builder.clients.model_unload.unload_model", spy)
    main(["ocr", "--input", str(_make_page(tmp_path / "p.jpg")), "--work", str(work_csv)])

    assert seen == [FakeOCRClient.endpoint]


# ── run-all（Phase 2：ocr → extract → pack）──────────────────────


def test_run_all_goes_from_image_to_zip(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, fake_llm, capsys
) -> None:
    page = _make_page(tmp_path / "pages" / "page1.jpg")
    output = tmp_path / "deck.zip"

    code = main(
        ["run-all", "--input", str(page), "--work", str(work_csv), "--output", str(output)]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "① ocr" in out and "② extract" in out and "⑦ pack" in out
    with zipfile.ZipFile(output) as archive:
        assert "cards.csv" in archive.namelist()


@pytest.mark.asyncio
async def test_fresh_clears_existing_rows_and_backs_up(
    env: pytest.MonkeyPatch, work_csv: Path
) -> None:
    """`--fresh` 的用途就是換一批教材時免去手動刪檔。"""
    from anki_deck_builder import cli
    from anki_deck_builder.state import CardStore

    store = CardStore(work_csv)
    await store.write([CardRow(card_id="old_001", front="舊卡", back="舊釋義")])

    await cli._clear_for_fresh_run(store)

    assert await store.read() == []
    backups = list(work_csv.parent.glob(f"{work_csv.name}.bak-*"))
    assert backups, "--fresh 必須先備份，清空是不可逆的"


@pytest.mark.asyncio
async def test_fresh_is_a_noop_on_an_empty_work_file(
    env: pytest.MonkeyPatch, work_csv: Path
) -> None:
    """沒有東西可清時不要製造空備份檔。"""
    from anki_deck_builder import cli
    from anki_deck_builder.state import CardStore

    store = CardStore(work_csv)
    await store.write([])

    await cli._clear_for_fresh_run(store)

    assert not list(work_csv.parent.glob(f"{work_csv.name}.bak-*"))


def test_run_all_without_fresh_keeps_existing_rows(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, fake_llm, capsys
) -> None:
    """**預設不清空**——`run-all` 同時是中斷續作的路徑，無條件覆寫會毀掉已完成的工作。"""
    _write_sync(work_csv, [CardRow(card_id="keep_001", front="保留", back="釋義")])
    page = _make_page(tmp_path / "pages" / "page1.jpg")

    main(["run-all", "--input", str(page), "--work", str(work_csv),
          "--output", str(tmp_path / "deck.zip")])

    assert "keep_001" in work_csv.read_text(encoding="utf-8-sig")


def test_run_all_does_not_stop_on_stage_failure(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, fake_llm, capsys
) -> None:
    """一頁壞掉不該讓整趟停在第一階段——後面照跑，最後一次看到全部問題。"""
    pages = tmp_path / "pages"
    _make_page(pages / "page1.jpg")
    (pages / "page2.jpg").write_bytes(b"not an image")
    output = tmp_path / "deck.zip"

    code = main(
        ["run-all", "--input", str(pages), "--work", str(work_csv), "--output", str(output)]
    )

    assert code == 1
    captured = capsys.readouterr()
    assert "② extract" in captured.out  # 沒有停在 ocr
    # 問題累積到 pack 一次擋下，並指路
    assert "pack 中止" in captured.err
    assert "--only-failed" in captured.err


def test_run_all_without_input_and_without_work_file_errors(
    env: pytest.MonkeyPatch, work_csv: Path, fake_ocr, fake_llm, capsys
) -> None:
    code = main(["run-all", "--work", str(work_csv)])

    assert code == 1
    assert "未指定 --input" in capsys.readouterr().err


def test_agent_commands_disable_trace_upload(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, monkeypatch
) -> None:
    """本地 Ollama 沒有真金鑰，開著 tracing 會每次噴 401。"""
    seen: list[bool] = []
    monkeypatch.setattr("agents.set_tracing_disabled", lambda flag: seen.append(flag))

    main(["ocr", "--input", str(_make_page(tmp_path / "p.jpg")), "--work", str(work_csv)])

    assert seen == [True]


def test_status_does_not_touch_agent_runtime(
    env: pytest.MonkeyPatch, work_csv: Path, monkeypatch
) -> None:
    """status 不呼叫模型，不該為 agent_factory 付出啟動成本。"""
    seen: list[bool] = []
    monkeypatch.setattr("agents.set_tracing_disabled", lambda flag: seen.append(flag))
    _write_sync(work_csv, [CardRow(card_id="a1", front="x", back="y")])

    main(["status", "--work", str(work_csv)])

    assert seen == []


def test_stage_frees_vram_before_running_by_default(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, monkeypatch
) -> None:
    """兩個模型在 24 GB 卡上無法共存，預設就該先騰位。"""
    seen: list[tuple[str, str]] = []

    async def spy(base_url: str, keep: str, **kwargs: object) -> list[str]:
        seen.append((base_url, keep))
        return []

    monkeypatch.setattr("anki_deck_builder.clients.model_unload.ensure_room", spy)
    main(["ocr", "--input", str(_make_page(tmp_path / "p.jpg")), "--work", str(work_csv)])

    assert seen == [FakeOCRClient.endpoint]


def test_freeing_vram_can_be_disabled(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, monkeypatch
) -> None:
    env.setenv("MODEL_UNLOAD_BEFORE_STAGE", "false")
    seen: list[str] = []

    async def spy(*args: object, **kwargs: object) -> list[str]:
        seen.append("called")
        return []

    monkeypatch.setattr("anki_deck_builder.clients.model_unload.ensure_room", spy)
    main(["ocr", "--input", str(_make_page(tmp_path / "p.jpg")), "--work", str(work_csv)])

    assert seen == []


def test_vision_direct_does_not_free_vram_for_ocr(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, monkeypatch
) -> None:
    """vision_direct 下 ocr 不呼叫模型，沒有理由動別人的 VRAM。"""
    env.setenv("INGEST_MODE", "vision_direct")
    seen: list[str] = []

    async def spy(*args: object, **kwargs: object) -> list[str]:
        seen.append("called")
        return []

    monkeypatch.setattr("anki_deck_builder.clients.model_unload.ensure_room", spy)
    main(["ocr", "--input", str(_make_page(tmp_path / "p.jpg")), "--work", str(work_csv)])

    assert seen == []


# ── image 子命令 ─────────────────────────────────────────────────


def _image_row(card_id: str = "a1", **overrides: object) -> CardRow:
    values: dict[str, object] = {
        "card_id": card_id,
        "front": "属する",
        "back": "屬於",
        "deck": "日語::N2",
        "card_type": "vocab",
        "image_prompt": "a tiger among cats, no text",
    }
    values.update(overrides)
    return CardRow(**values)  # type: ignore[arg-type]


def test_image_generates_and_reports(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, capsys
) -> None:
    _write_sync(work_csv, [_image_row()])

    code = main(["image", "--work", str(work_csv)])

    assert code == 0
    assert "image：處理 1 列（成功 1、失敗 0）" in capsys.readouterr().out
    assert FakeComfyUIClient.calls == [("a tiger among cats, no text", stable_seed("a1"))]
    row = _read_sync(work_csv)[0]
    assert row.image_front == "media/img/a1.webp"
    assert (work_csv.parent / "media" / "img" / "a1.webp").read_bytes()[:4] == b"RIFF"  # WebP 容器


def test_image_returns_error_code_when_a_row_fails(
    env: pytest.MonkeyPatch, work_csv: Path, capsys
) -> None:
    FakeComfyUIClient.error = ExternalServiceError("ComfyUI 執行失敗：CUDA out of memory")
    _write_sync(work_csv, [_image_row()])

    code = main(["image", "--work", str(work_csv)])

    assert code == 1
    assert "status" in capsys.readouterr().err
    assert _read_sync(work_csv)[0].image_status is StageStatus.FAILED


def test_image_only_failed_selects_failed_rows(
    env: pytest.MonkeyPatch, work_csv: Path
) -> None:
    _write_sync(
        work_csv,
        [
            _image_row("a1", image_status=StageStatus.DONE, image_front="media/img/a1.webp"),
            _image_row("a2", image_status=StageStatus.FAILED, image_error="上次逾時"),
        ],
    )

    assert main(["image", "--work", str(work_csv), "--only-failed"]) == 0
    assert [seed for _, seed in FakeComfyUIClient.calls] == [stable_seed("a2")]


def test_image_reports_configuration_errors(
    env: pytest.MonkeyPatch, work_csv: Path, monkeypatch, capsys
) -> None:
    """節點 ID 打錯要在生成開始前擋下，訊息直指哪個環境變數。"""

    def reject(self: object) -> None:
        raise ConfigurationError("注入點「正向 prompt」（COMFYUI_POSITIVE_NODE_ID=99）對不上")

    monkeypatch.setattr(FakeComfyUIClient, "validate", reject)
    _write_sync(work_csv, [_image_row()])

    code = main(["image", "--work", str(work_csv)])

    assert code == 1
    assert "COMFYUI_POSITIVE_NODE_ID=99" in capsys.readouterr().err
    assert FakeComfyUIClient.calls == []


def test_run_all_includes_image_between_extract_and_pack(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, fake_llm, capsys
) -> None:
    page = _make_page(tmp_path / "pages" / "page1.jpg")
    output = tmp_path / "deck.zip"

    code = main(
        ["run-all", "--input", str(page), "--work", str(work_csv), "--output", str(output)]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert (
        out.index("② extract")
        < out.index("③ scene")
        < out.index("④ prompt")
        < out.index("⑤ image")
        < out.index("⑦ pack")
    )
    with zipfile.ZipFile(output) as archive:
        assert "media/img/p1_001.webp" in archive.namelist()


# ── audio 子命令 ─────────────────────────────────────────────────


def _audio_row(card_id: str = "a1", **overrides: object) -> CardRow:
    values: dict[str, object] = {
        "card_id": card_id,
        "front": "属する",
        "back": "屬於",
        "deck": "日語::N2",
        "card_type": "vocab",
        "tts_front_text": "属する",
        "tts_back_text": "虎はネコ科に属する。",
    }
    values.update(overrides)
    return CardRow(**values)  # type: ignore[arg-type]


def test_side_defaults_to_both() -> None:
    assert build_parser().parse_args(["audio"]).side == "both"


@pytest.mark.parametrize("side", ["front", "back", "both"])
def test_side_accepts_the_three_values(side: str) -> None:
    assert build_parser().parse_args(["audio", "--side", side]).side == side


def test_side_rejects_anything_else() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["audio", "--side", "middle"])


def test_audio_both_generates_two_files(
    env: pytest.MonkeyPatch, work_csv: Path, capsys
) -> None:
    _write_sync(work_csv, [_audio_row()])

    code = main(["audio", "--work", str(work_csv)])

    assert code == 0
    out = capsys.readouterr().out
    assert "audio_front：處理 1 列（成功 1、失敗 0）" in out
    assert "audio_back：處理 1 列（成功 1、失敗 0）" in out
    row = _read_sync(work_csv)[0]
    assert row.audio_front == "media/audio/a1_front.mp3"
    assert row.audio_back == "media/audio/a1_back.mp3"
    assert (work_csv.parent / "media" / "audio" / "a1_front.mp3").stat().st_size > 0


def test_side_front_leaves_back_untouched(
    env: pytest.MonkeyPatch, work_csv: Path, capsys
) -> None:
    """`--side front` 不該碰到 audio_back 的任何東西。"""
    _write_sync(work_csv, [_audio_row()])

    assert main(["audio", "--work", str(work_csv), "--side", "front"]) == 0

    out = capsys.readouterr().out
    assert "audio_front" in out and "audio_back" not in out
    row = _read_sync(work_csv)[0]
    assert row.audio_front_status is StageStatus.DONE
    assert row.audio_back_status is StageStatus.PENDING
    assert row.audio_back == ""
    assert FakeVoxCPMClient.calls == ["属する"]


def test_side_back_only_synthesizes_the_sentence(
    env: pytest.MonkeyPatch, work_csv: Path
) -> None:
    _write_sync(work_csv, [_audio_row()])

    assert main(["audio", "--work", str(work_csv), "--side", "back"]) == 0

    assert FakeVoxCPMClient.calls == ["虎はネコ科に属する。"]
    assert _read_sync(work_csv)[0].audio_front_status is StageStatus.PENDING


def test_audio_returns_error_code_when_a_row_fails(
    env: pytest.MonkeyPatch, work_csv: Path, capsys
) -> None:
    FakeVoxCPMClient.error = ExternalServiceError("VOXCPM2 生成失敗（RuntimeError: CUDA OOM）")
    _write_sync(work_csv, [_audio_row()])

    code = main(["audio", "--work", str(work_csv)])

    assert code == 1
    assert "status" in capsys.readouterr().err
    assert _read_sync(work_csv)[0].audio_front_status is StageStatus.FAILED


def test_audio_only_failed_selects_failed_rows(
    env: pytest.MonkeyPatch, work_csv: Path
) -> None:
    _write_sync(
        work_csv,
        [
            _audio_row("a1", audio_front_status=StageStatus.DONE),
            _audio_row("a2", audio_front_status=StageStatus.FAILED, audio_front_error="上次失敗"),
        ],
    )

    assert main(["audio", "--work", str(work_csv), "--side", "front", "--only-failed"]) == 0
    assert FakeVoxCPMClient.calls == ["属する"]


def test_audio_reports_configuration_errors(
    env: pytest.MonkeyPatch, work_csv: Path, monkeypatch, capsys
) -> None:
    """模型載入要 77 秒，設定錯誤要在那之前擋下。"""

    def reject(self: object) -> None:
        raise ConfigurationError("VOXCPM2_MODEL_PATH 指向的目錄不存在：/nope")

    monkeypatch.setattr(FakeVoxCPMClient, "validate", reject)
    _write_sync(work_csv, [_audio_row()])

    code = main(["audio", "--work", str(work_csv)])

    assert code == 1
    assert "VOXCPM2_MODEL_PATH" in capsys.readouterr().err
    assert FakeVoxCPMClient.calls == []


def test_run_all_runs_audio_after_image_and_before_pack(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, fake_llm, capsys
) -> None:
    page = _make_page(tmp_path / "pages" / "page1.jpg")
    output = tmp_path / "deck.zip"

    code = main(
        ["run-all", "--input", str(page), "--work", str(work_csv), "--output", str(output)]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert (
        out.index("⑤ image")
        < out.index("⑥ audio_front")
        < out.index("⑥ audio_back")
        < out.index("⑦ pack")
    )
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    assert "media/audio/p1_001_front.mp3" in names
    assert "media/audio/p1_001_back.mp3" in names


# ── VRAM 讓渡的接線 ──────────────────────────────────────────────


def test_image_unloads_every_ollama_model(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm, monkeypatch
) -> None:
    """抽取模型獨佔 20.3 GB，image 開工前必須全卸——keep 傳空字串。"""
    seen: list[tuple[str, str]] = []

    async def spy(base_url: str, keep: str, **kwargs: object) -> list[str]:
        seen.append((base_url, keep))
        return []

    monkeypatch.setattr("anki_deck_builder.clients.model_unload.ensure_room", spy)
    _write_sync(work_csv, [_image_row()])

    main(["image", "--work", str(work_csv)])

    assert seen == [("http://localhost:11434/v1/", "")]


def test_image_respects_the_unload_switch(
    env: pytest.MonkeyPatch, work_csv: Path, monkeypatch
) -> None:
    """關掉開關時連 agents.yaml 都不該去讀——image 自己不呼叫任何 agent。"""
    env.setenv("MODEL_UNLOAD_BEFORE_STAGE", "false")
    seen: list[str] = []

    async def spy(*args: object, **kwargs: object) -> list[str]:
        seen.append("ensure_room")
        return []

    monkeypatch.setattr("anki_deck_builder.clients.model_unload.ensure_room", spy)
    monkeypatch.setattr(
        "anki_deck_builder.clients.llm_client.LLMClient",
        lambda *a, **k: pytest.fail("關閉讓渡時不該建立 LLMClient"),
    )
    _write_sync(work_csv, [_image_row()])

    assert main(["image", "--work", str(work_csv)]) == 0
    assert seen == []


def test_extract_asks_comfyui_to_free_vram_when_enabled(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm, monkeypatch, capsys
) -> None:
    """ComfyUI 常駐 2.4 GB 會讓抽取模型載不滿，開啟後 extract 前先請它讓位。"""
    env.setenv("COMFYUI_FREE_BEFORE_LLM", "true")
    seen: list[str] = []

    async def spy(base_url: str, **kwargs: object) -> bool:
        seen.append(base_url)
        return True

    monkeypatch.setattr("anki_deck_builder.clients.comfyui_client.free_memory", spy)
    _write_sync(work_csv, [CardRow(raw_text="ability (n) 能力", ocr_source_page=1)])

    main(["extract", "--work", str(work_csv)])

    assert seen == ["http://127.0.0.1:8188"]
    assert "已請 ComfyUI 釋放 VRAM" in capsys.readouterr().out


def test_comfyui_free_is_off_by_default(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm, monkeypatch
) -> None:
    """開啟有代價（ComfyUI 下次生成要重載模型），預設不動它。"""
    seen: list[str] = []

    async def spy(base_url: str, **kwargs: object) -> bool:
        seen.append(base_url)
        return True

    monkeypatch.setattr("anki_deck_builder.clients.comfyui_client.free_memory", spy)
    _write_sync(work_csv, [CardRow(raw_text="ability (n) 能力", ocr_source_page=1)])

    main(["extract", "--work", str(work_csv)])

    assert seen == []


def test_audio_unloads_every_ollama_model(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm, monkeypatch
) -> None:
    """audio 在本機 GPU 上跑自己的模型，不需要任何 Ollama 模型。"""
    seen: list[tuple[str, str]] = []

    async def spy(base_url: str, keep: str, **kwargs: object) -> list[str]:
        seen.append((base_url, keep))
        return []

    monkeypatch.setattr("anki_deck_builder.clients.model_unload.ensure_room", spy)
    _write_sync(work_csv, [_audio_row()])

    main(["audio", "--work", str(work_csv)])

    assert seen == [("http://localhost:11434/v1/", "")]


def test_audio_frees_vram_once_for_both_sides(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm, monkeypatch
) -> None:
    """兩側共用一次讓渡與一個 client——模型建構要 77 秒，各建一次等於白等一輪。"""
    calls: list[str] = []

    async def spy(*args: object, **kwargs: object) -> list[str]:
        calls.append("ensure_room")
        return []

    monkeypatch.setattr("anki_deck_builder.clients.model_unload.ensure_room", spy)
    _write_sync(work_csv, [_audio_row()])

    main(["audio", "--work", str(work_csv), "--side", "both"])

    assert len(calls) == 1


def test_run_all_releases_the_gpu_before_the_first_stage(
    env: pytest.MonkeyPatch, work_csv: Path, tmp_path: Path, fake_ocr, fake_llm, monkeypatch
) -> None:
    """**開跑前一定要清 GPU，而且不看 COMFYUI_FREE_BEFORE_LLM。**

    階段之間的讓渡假設「這個 pipeline 是 GPU 上唯一的東西」，開跑前 GPU 已經
    有東西時那個假設不成立——ComfyUI 只要被用過就抓著 17.4 GB 不放，
    第一階段一頭撞上去就是 CUDA OOM（2026-09-02 使用者實測遇到）。
    """
    freed: list[str] = []

    async def comfy_spy(base_url: str, **kwargs: object) -> bool:
        freed.append(f"comfyui:{base_url}")
        return True

    async def ollama_spy(base_url: str, keep: str, **kwargs: object) -> list[str]:
        freed.append(f"ollama:keep={keep!r}")
        return ["gemma4-e4b-optimized:latest"]

    monkeypatch.setattr("anki_deck_builder.clients.comfyui_client.free_memory", comfy_spy)
    monkeypatch.setattr("anki_deck_builder.clients.model_unload.ensure_room", ollama_spy)
    page = _make_page(tmp_path / "pages" / "page1.jpg")

    main(["run-all", "--input", str(page), "--work", str(work_csv),
          "--output", str(tmp_path / "deck.zip")])

    # 預設 COMFYUI_FREE_BEFORE_LLM 是 false，開跑前那次仍必須發生
    assert "comfyui:http://127.0.0.1:8188" in freed
    # keep="" ＝ 一個都不留：此刻 GPU 上沒有一樣東西是這趟流程需要的
    assert "ollama:keep=''" in freed


def test_local_gpu_release_also_frees_voxcpm(monkeypatch) -> None:
    """VOXCPM2 是行程內的模型，沒有可以打的端點——只能還 PyTorch 的快取。

    CLI 每個指令是獨立行程所以感覺不到，但 Web UI 是長駐行程：
    先跑 audio 再跑 image 時，那約 7.5 GB 會一直卡著 ComfyUI。
    """
    import sys
    import types

    from anki_deck_builder.clients import tts_client

    emptied: list[bool] = []
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=lambda: True,
            empty_cache=lambda: emptied.append(True),
        )
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert tts_client.release_gpu_cache() is True
    assert emptied == [True]


def test_voxcpm_release_is_a_noop_when_torch_was_never_imported(monkeypatch) -> None:
    """全新的 CLI 行程沒載過 torch——不該為了清一塊空快取而匯入它。"""
    import sys

    from anki_deck_builder.clients import tts_client

    monkeypatch.delitem(sys.modules, "torch", raising=False)

    assert tts_client.release_gpu_cache() is False


def test_audio_asks_comfyui_to_free_vram_when_enabled(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm, monkeypatch, capsys
) -> None:
    """VOXCPM2 峰值 7.5 GB 要放得下 ComfyUI 常駐——kyoani workflow 的 17.4 GB
    合計會超出 24 GB 卡，所以 audio 前也得請它讓位（`_free_vram_for_local_gpu`
    只卸載 Ollama 的模型，不碰 ComfyUI）。"""
    env.setenv("COMFYUI_FREE_BEFORE_LLM", "true")
    seen: list[str] = []

    async def spy(base_url: str, **kwargs: object) -> bool:
        seen.append(base_url)
        return True

    monkeypatch.setattr("anki_deck_builder.clients.comfyui_client.free_memory", spy)
    _write_sync(work_csv, [_audio_row()])

    main(["audio", "--work", str(work_csv)])

    assert seen == ["http://127.0.0.1:8188"]
    assert "已請 ComfyUI 釋放 VRAM" in capsys.readouterr().out


def test_audio_does_not_ask_comfyui_to_free_vram_by_default(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm, monkeypatch
) -> None:
    """同 extract：開啟有代價（ComfyUI 下次生成要重載模型），預設不動它。"""
    seen: list[str] = []

    async def spy(base_url: str, **kwargs: object) -> bool:
        seen.append(base_url)
        return True

    monkeypatch.setattr("anki_deck_builder.clients.comfyui_client.free_memory", spy)
    _write_sync(work_csv, [_audio_row()])

    main(["audio", "--work", str(work_csv)])

    assert seen == []


def test_image_does_not_ask_comfyui_to_free_vram(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm, monkeypatch
) -> None:
    """讓位是給 LLM 的，image 正要用 ComfyUI——這時清空它只是白費工。"""
    env.setenv("COMFYUI_FREE_BEFORE_LLM", "true")
    seen: list[str] = []

    async def spy(base_url: str, **kwargs: object) -> bool:
        seen.append(base_url)
        return True

    monkeypatch.setattr("anki_deck_builder.clients.comfyui_client.free_memory", spy)
    _write_sync(work_csv, [_image_row()])

    main(["image", "--work", str(work_csv)])

    assert seen == []


def test_card_language_flag_reaches_the_stage(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm, capsys
) -> None:
    _write_sync(work_csv, [CardRow(raw_text="ability (n)", ocr_source_page=1)])

    code = main(
        ["extract", "--work", str(work_csv), "--card-language", "繁體中文"]
    )

    assert code == 0
    sent = fake_llm.last_input
    assert "釋義語言: 繁體中文" in sent


def test_deck_categories_flag_reaches_the_stage(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm
) -> None:
    _write_sync(work_csv, [CardRow(raw_text="□β受體阻斷劑\n[藥理分類] 阻斷…", ocr_source_page=1)])

    code = main(
        ["extract", "--work", str(work_csv), "--deck-categories", "心血管藥物／其他"]
    )

    assert code == 0
    assert "分類選項: 心血管藥物／其他" in fake_llm.last_input


def test_enrich_flags_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["extract", "--enrich", "--no-enrich"])


def test_enrich_defaults_to_auto_detection() -> None:
    """預設不強制——由 MaterialTypeAgent 判斷，使用者不必記得加旗標。"""
    assert build_parser().parse_args(["extract"]).enrich is None
    assert build_parser().parse_args(["extract", "--enrich"]).enrich is True
    assert build_parser().parse_args(["extract", "--no-enrich"]).enrich is False


def test_auto_detection_runs_by_default(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm
) -> None:
    _write_sync(work_csv, [CardRow(raw_text="□属する\nぞくする", ocr_source_page=1)])

    assert main(["extract", "--work", str(work_csv)]) == 0
    assert fake_llm.detect_calls == 1


def test_explicit_flag_skips_detection(
    env: pytest.MonkeyPatch, work_csv: Path, fake_llm
) -> None:
    _write_sync(work_csv, [CardRow(raw_text="□属する\nぞくする", ocr_source_page=1)])

    assert main(["extract", "--work", str(work_csv), "--no-enrich"]) == 0
    assert fake_llm.detect_calls == 0
