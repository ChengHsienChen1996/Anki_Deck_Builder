"""CLI 單元測試（非付費模組，AI 執行至通過）。

`extract` 的 LLM 呼叫以假 client 取代，測試不觸及任何模型。
"""

import zipfile
from pathlib import Path

import pytest

from anki_deck_builder.cli import build_parser, main
from anki_deck_builder.schemas import CardRow, ExtractedCard, ExtractOutput, StageStatus
from anki_deck_builder.state import CardStore

SUBCOMMANDS = (
    "ocr",
    "extract",
    "image",
    "audio",
    "pack",
    "run-all",
    "status",
    "serve",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    # 切到空目錄：load_settings() 預設會讀相對路徑的 .env，
    # 在專案根目錄執行時會撈到開發者的真實設定，讓測試結果依環境而異
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("YAML_SETTINGS_FILE", "agents.yaml")
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.delenv("INGEST_MODE", raising=False)
    monkeypatch.delenv("GLOBAL_CONCURRENCY", raising=False)
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

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls = 0

    async def run_agent(self, agent_name: str, input_: object) -> ExtractOutput:
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
    FakeLLMClient.responses = [
        ExtractOutput(
            cards=[
                ExtractedCard(
                    card_id="ja_n2_p1_001",
                    deck="日語::N2::動詞",
                    front="属する",
                    back="屬於，歸於",
                    reading="ぞくする",
                )
            ]
        )
    ]
    monkeypatch.setattr(
        "anki_deck_builder.clients.llm_client.LLMClient", FakeLLMClient
    )
    return FakeLLMClient


# ── 參數解析 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_every_subcommand_is_defined(command: str) -> None:
    args = build_parser().parse_args([command])

    assert args.command == command


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


# ── 尚未實作的子命令 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("command", "phase"),
    [
        ("image", "Phase 3"),
        ("audio", "Phase 4"),
        ("serve", "Phase 5"),
    ],
)
def test_unimplemented_commands_report_without_crashing(
    command: str, phase: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([command])

    assert code == 0
    assert phase in capsys.readouterr().out


def test_unimplemented_commands_do_not_need_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("YAML_SETTINGS_FILE", raising=False)

    assert main(["image"]) == 0
    assert "Phase 3" in capsys.readouterr().out


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
    assert "① ocr" in out and "② extract" in out and "⑤ pack" in out
    with zipfile.ZipFile(output) as archive:
        assert "cards.csv" in archive.namelist()


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
