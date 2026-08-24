"""設定層單元測試（非付費模組，AI 執行至通過）。"""

from pathlib import Path

import pytest
from pydantic import SecretStr

from anki_deck_builder.config import (
    ComfyUINodeSettings,
    load_settings,
    mask_secret,
)
from anki_deck_builder.exceptions import AnkiBuilderError, ConfigurationError

# 所有本專案會讀到的環境變數，測試前一律清空，避免開發機的真實 .env 汙染結果
ENV_VARS = [
    "OPENAI_API_KEY",
    "YAML_SETTINGS_FILE",
    "GLOBAL_CONCURRENCY",
    "RPM",
    "TPM",
    "INGEST_MODE",
    "WORK_DIR",
    "OUTPUT_DIR",
    "COMFYUI_BASE_URL",
    "COMFYUI_WORKFLOW_PATH",
    "COMFYUI_POLL_INTERVAL",
    "COMFYUI_TIMEOUT",
    "COMFYUI_BATCH_SIZE",
    "COMFYUI_IMAGE_WIDTH",
    "COMFYUI_IMAGE_HEIGHT",
    "COMFYUI_NEGATIVE_PROMPT",
    "COMFYUI_POSITIVE_NODE_ID",
    "COMFYUI_POSITIVE_FIELD",
    "COMFYUI_NEGATIVE_NODE_ID",
    "COMFYUI_NEGATIVE_FIELD",
    "COMFYUI_SEED_NODE_ID",
    "COMFYUI_SEED_FIELD",
    "COMFYUI_LATENT_NODE_ID",
    "COMFYUI_WIDTH_FIELD",
    "COMFYUI_HEIGHT_FIELD",
    "COMFYUI_OUTPUT_NODE_ID",
    "VOXCPM2_MODEL_PATH",
    "VOXCPM2_DEVICE",
    "VOXCPM2_REFERENCE_WAV",
    "VOXCPM2_PROMPT_WAV",
    "VOXCPM2_PROMPT_TEXT",
    "VOXCPM2_CFG_VALUE",
    "VOXCPM2_INFERENCE_TIMESTEPS",
    "VOXCPM2_NORMALIZE",
    "VOXCPM2_ENABLE_DENOISER",
    "VOXCPM2_DENOISE",
    "VOXCPM2_OPTIMIZE",
    "VOXCPM2_CONCURRENCY",
]

REQUIRED = {
    "OPENAI_API_KEY": "ollama",
    "YAML_SETTINGS_FILE": "agents.yaml",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ── 正常載入 ─────────────────────────────────────────────────────


def test_load_with_required_only_uses_defaults(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)

    settings = load_settings(env_file=None)

    assert settings.agent_factory.openai_api_key.get_secret_value() == "ollama"
    assert settings.agent_factory.yaml_settings_file == Path("agents.yaml")
    assert settings.agent_factory.global_concurrency == 6
    assert settings.agent_factory.rpm == 200
    assert settings.agent_factory.tpm == 30000
    assert settings.ingest.mode == "two_stage"
    assert settings.ingest.pdf_dpi == 200
    assert settings.model_unload.enabled is False
    assert settings.model_unload.before_stage is True
    assert settings.model_unload.timeout == 30.0
    assert settings.paths.work_dir == Path("./work")
    assert settings.paths.output_dir == Path("./output")
    assert settings.comfyui.base_url == "http://127.0.0.1:8188"
    assert settings.comfyui.batch_size == 4
    assert settings.comfyui.image_width == 768
    assert settings.comfyui.image_height == 432
    assert settings.comfyui.free_before_llm is False
    assert settings.tts.model_path is None
    assert settings.tts.cfg_value == 2.0
    assert settings.tts.inference_timesteps == 10
    assert settings.tts.enable_denoiser is False
    assert settings.tts.concurrency == 1
    assert settings.tts.uses_random_voice is True


def test_load_full_env(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("GLOBAL_CONCURRENCY", "2")
    clean_env.setenv("INGEST_MODE", "vision_direct")
    clean_env.setenv("WORK_DIR", "/tmp/w")
    clean_env.setenv("OUTPUT_DIR", "/tmp/o")
    clean_env.setenv("COMFYUI_POLL_INTERVAL", "0.5")
    clean_env.setenv("VOXCPM2_MODEL_PATH", "/models/voxcpm2")
    clean_env.setenv("VOXCPM2_CFG_VALUE", "1.25")
    clean_env.setenv("VOXCPM2_OPTIMIZE", "false")

    settings = load_settings(env_file=None)

    assert settings.agent_factory.global_concurrency == 2
    assert settings.ingest.mode == "vision_direct"
    assert settings.paths.work_dir == Path("/tmp/w")
    assert settings.paths.output_dir == Path("/tmp/o")
    assert settings.comfyui.poll_interval == 0.5
    assert settings.tts.model_path == Path("/models/voxcpm2")
    assert settings.tts.cfg_value == 1.25
    assert settings.tts.optimize is False


def test_pdf_dpi_is_configurable(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("INGEST_PDF_DPI", "300")

    settings = load_settings(env_file=None)

    assert settings.ingest.pdf_dpi == 300


def test_model_unload_is_configurable(clean_env: pytest.MonkeyPatch) -> None:
    """VRAM 讓渡預設關閉——它是最佳化，不是流程的一部分。"""
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("MODEL_UNLOAD_ENABLED", "true")
    clean_env.setenv("MODEL_UNLOAD_TIMEOUT", "45")

    settings = load_settings(env_file=None)

    assert settings.model_unload.enabled is True
    assert settings.model_unload.timeout == 45.0


def test_nested_node_settings_are_grouped(clean_env: pytest.MonkeyPatch) -> None:
    """節點注入點必須經 settings.comfyui.nodes.* 存取（巢狀分組）。"""
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("COMFYUI_POSITIVE_NODE_ID", "6")
    clean_env.setenv("COMFYUI_POSITIVE_FIELD", "text")
    clean_env.setenv("COMFYUI_OUTPUT_NODE_ID", "9")

    settings = load_settings(env_file=None)

    assert isinstance(settings.comfyui.nodes, ComfyUINodeSettings)
    assert settings.comfyui.nodes.positive_node_id == "6"
    assert settings.comfyui.nodes.positive_field == "text"
    assert settings.comfyui.nodes.output_node_id == "9"
    # 選填注入點未設定時留空，由 Phase 3 判斷「留空即不注入」
    assert settings.comfyui.nodes.seed_node_id == ""


def test_cards_csv_derives_from_work_dir(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("WORK_DIR", "/tmp/w")

    settings = load_settings(env_file=None)

    assert settings.paths.cards_csv == Path("/tmp/w/cards.csv")


# ── 缺必填 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_names_the_variable(
    clean_env: pytest.MonkeyPatch, missing: str
) -> None:
    for k, v in REQUIRED.items():
        if k != missing:
            clean_env.setenv(k, v)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert missing in str(excinfo.value)
    assert "缺少必填環境變數" in str(excinfo.value)


def test_missing_all_required_lists_every_variable(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    message = str(excinfo.value)
    for name in REQUIRED:
        assert name in message


# ── 型別轉換失敗 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "bad_value"),
    [
        ("GLOBAL_CONCURRENCY", "many"),
        ("COMFYUI_TIMEOUT", "abc"),
        ("COMFYUI_POLL_INTERVAL", "slow"),
        ("VOXCPM2_CFG_VALUE", "fast"),
        ("INGEST_MODE", "magic"),
        ("INGEST_PDF_DPI", "0"),
        ("INGEST_PDF_DPI", "9999"),
        ("INGEST_PDF_DPI", "not-a-number"),
    ],
)
def test_invalid_value_names_variable_and_value(
    clean_env: pytest.MonkeyPatch, name: str, bad_value: str
) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv(name, bad_value)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    message = str(excinfo.value)
    assert name in message
    assert bad_value in message


def test_configuration_error_is_project_error() -> None:
    assert issubclass(ConfigurationError, AnkiBuilderError)


# ── 階段性驗證：不在載入時檢查外部資源 ───────────────────────────


def test_missing_workflow_file_does_not_block_loading(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """COMFYUI_WORKFLOW_PATH 指向不存在的檔案時仍須載入成功。

    存在性檢查屬 image 階段，放在載入時會讓 Phase 1 被無關設定擋住。
    """
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    missing = tmp_path / "nope" / "card_image.json"
    clean_env.setenv("COMFYUI_WORKFLOW_PATH", str(missing))

    settings = load_settings(env_file=None)

    assert settings.comfyui.workflow_path == missing
    assert not missing.exists()


def test_unreachable_services_do_not_block_loading(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:1")

    settings = load_settings(env_file=None)

    assert settings.comfyui.base_url == "http://127.0.0.1:1"


def test_missing_model_path_does_not_block_loading(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """模型目錄與參考音檔存不存在屬階段性驗證，留給 audio 階段檢查。"""
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    missing = tmp_path / "nope"
    clean_env.setenv("VOXCPM2_MODEL_PATH", str(missing))
    clean_env.setenv("VOXCPM2_REFERENCE_WAV", str(missing / "ref.wav"))

    settings = load_settings(env_file=None)

    assert settings.tts.model_path == missing
    assert not missing.exists()


# ── 音色設定 ─────────────────────────────────────────────────────


def test_reference_wav_turns_off_random_voice(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("VOXCPM2_REFERENCE_WAV", "/voices/narrator.wav")

    settings = load_settings(env_file=None)

    assert settings.tts.reference_wav == Path("/voices/narrator.wav")
    assert settings.tts.uses_random_voice is False


@pytest.mark.parametrize(
    "provided", ["VOXCPM2_PROMPT_WAV", "VOXCPM2_PROMPT_TEXT"]
)
def test_prompt_pair_must_be_complete(
    clean_env: pytest.MonkeyPatch, provided: str
) -> None:
    """continuation 模式缺一方時 voxcpm 會直接拋錯，設定載入時就要擋下。"""
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv(provided, "/voices/narrator.wav" if "WAV" in provided else "こんにちは")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    message = str(excinfo.value)
    assert "VOXCPM2_PROMPT_WAV" in message
    assert "VOXCPM2_PROMPT_TEXT" in message


def test_complete_prompt_pair_is_accepted(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("VOXCPM2_PROMPT_WAV", "/voices/narrator.wav")
    clean_env.setenv("VOXCPM2_PROMPT_TEXT", "こんにちは")

    settings = load_settings(env_file=None)

    assert settings.tts.prompt_text == "こんにちは"
    assert settings.tts.uses_random_voice is False


# ── env_file 載入 ────────────────────────────────────────────────


def test_reads_from_env_file(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=ollama\n"
        "YAML_SETTINGS_FILE=agents.yaml\n"
        "COMFYUI_POSITIVE_NODE_ID=6\n"
        "VOXCPM2_CFG_VALUE=1.5\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file)

    assert settings.agent_factory.openai_api_key.get_secret_value() == "ollama"
    assert settings.comfyui.nodes.positive_node_id == "6"
    assert settings.tts.cfg_value == 1.5


# ── 金鑰遮罩 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sk-abc123", "sk-****23"),
        ("sk-proj-0123456789", "sk-****89"),
        ("ollama", "******"),
        ("12345", "*****"),
        ("", ""),
    ],
)
def test_mask_secret(raw: str, expected: str) -> None:
    assert mask_secret(raw) == expected


def test_mask_secret_accepts_secret_str_and_none() -> None:
    assert mask_secret(SecretStr("sk-abc123")) == "sk-****23"
    assert mask_secret(None) == ""


def test_masked_summary_hides_key(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENAI_API_KEY", "sk-abc123")
    clean_env.setenv("YAML_SETTINGS_FILE", "agents.yaml")

    summary = load_settings(env_file=None).masked_summary()

    assert summary["OPENAI_API_KEY"] == "sk-****23"
    assert summary["VOXCPM2_REFERENCE_WAV"] == "(隨機音色)"
    assert "sk-abc123" not in str(summary)


def test_blank_optional_paths_are_treated_as_unset(clean_env: pytest.MonkeyPatch) -> None:
    """`.env.example` 原樣複製會留下空值的 VOXCPM2_* 路徑，那等同未設定。

    沒有這層轉換時 pydantic 會把空字串變成 `Path('.')`——一個存在的目錄，
    既非 None 也非有效音檔，會讓成對性驗證誤判、`uses_random_voice` 誤判為否。
    """
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    for name in ("VOXCPM2_MODEL_PATH", "VOXCPM2_REFERENCE_WAV", "VOXCPM2_PROMPT_WAV"):
        clean_env.setenv(name, "")
    clean_env.setenv("VOXCPM2_PROMPT_TEXT", "")

    settings = load_settings(env_file=None)

    assert settings.tts.model_path is None
    assert settings.tts.reference_wav is None
    assert settings.tts.prompt_wav is None
    assert settings.tts.uses_random_voice is True
