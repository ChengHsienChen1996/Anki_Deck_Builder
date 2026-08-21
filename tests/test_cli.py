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

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls = 0

    async def run_agent(self, agent_name: str, input_: object) -> ExtractOutput:
        if FakeLLMClient.error is not None:
            raise FakeLLMClient.error
        self.calls += 1
        return FakeLLMClient.responses[self.calls - 1]


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
        ("ocr", "Phase 2"),
        ("image", "Phase 3"),
        ("audio", "Phase 4"),
        ("serve", "Phase 5"),
        ("run-all", "Phase 5"),
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
