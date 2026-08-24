"""VOXCPM2 client 單元測試。

模型以假工廠注入，**完全不載入真實權重**（4.58 GB、佔 VRAM、與 ComfyUI 搶資源），
也不匯入 `voxcpm`／torch——依 docs/llm-integration.md 對本地模型的要求。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from anki_deck_builder.clients.tts_client import VoxCPMClient
from anki_deck_builder.config import TTSSettings
from anki_deck_builder.exceptions import ConfigurationError, ExternalServiceError

SAMPLE_RATE = 48000


class FakeTTSModel:
    """記錄每次呼叫的假模型。回傳一段 0.2 秒的靜音波形。"""

    def __init__(self, error: Exception | None = None, sample_rate: int = SAMPLE_RATE) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tts_model = type("_Inner", (), {"sample_rate": sample_rate})()

    def generate(self, text: str, **kwargs: Any):  # noqa: ANN202
        self.calls.append((text, kwargs))
        if self.error is not None:
            raise self.error
        return np.zeros(int(SAMPLE_RATE * 0.2), dtype=np.float32)


def make_settings(tmp_path: Path, **overrides: Any) -> TTSSettings:
    """組出設定；每個欄位都明給，避免專案根目錄的 `.env` 滲進測試。"""
    model_dir = tmp_path / "model"
    model_dir.mkdir(exist_ok=True)
    values: dict[str, Any] = {
        "model_path": model_dir,
        "device": "",
        "voice_description": "",
        "reference_wav": None,
        "prompt_wav": None,
        "prompt_text": "",
        "cfg_value": 2.0,
        "inference_timesteps": 10,
        "normalize": False,
        "enable_denoiser": False,
        "denoise": False,
        "optimize": True,
        "concurrency": 1,
    }
    values.update(overrides)
    return TTSSettings(**values)


def make_wav(path: Path) -> Path:
    """寫一個真的 WAV 當參考音檔——`validate()` 只看存在與否，內容不重要。"""
    sf.write(str(path), np.zeros(1000, dtype=np.float32), SAMPLE_RATE)
    return path


@pytest.fixture
def client(tmp_path: Path) -> tuple[VoxCPMClient, FakeTTSModel]:
    model = FakeTTSModel()
    return VoxCPMClient(make_settings(tmp_path), model_factory=lambda: model), model


# ── validate ─────────────────────────────────────────────────────


def test_validate_passes(client) -> None:
    client[0].validate()


def test_validate_requires_model_path(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, model_path=None)

    with pytest.raises(ConfigurationError, match="VOXCPM2_MODEL_PATH 未設定"):
        VoxCPMClient(settings, model_factory=FakeTTSModel).validate()


def test_validate_rejects_missing_model_dir(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, model_path=tmp_path / "不存在")

    with pytest.raises(ConfigurationError, match="不存在"):
        VoxCPMClient(settings, model_factory=FakeTTSModel).validate()


def test_validate_rejects_missing_reference_wav(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, reference_wav=tmp_path / "no.wav")

    with pytest.raises(ConfigurationError) as exc:
        VoxCPMClient(settings, model_factory=FakeTTSModel).validate()

    assert "VOXCPM2_REFERENCE_WAV" in str(exc.value)


def test_validate_rejects_missing_prompt_wav(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path, prompt_wav=tmp_path / "no.wav", prompt_text="逐字稿"
    )

    with pytest.raises(ConfigurationError, match="VOXCPM2_PROMPT_WAV"):
        VoxCPMClient(settings, model_factory=FakeTTSModel).validate()


def test_validate_accepts_existing_voice_files(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, reference_wav=make_wav(tmp_path / "ref.wav"))

    VoxCPMClient(settings, model_factory=FakeTTSModel).validate()


# ── 生成 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_returns_wav_bytes(client) -> None:
    voice, model = client

    data = await voice.synthesize("属する")

    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    audio, rate = sf.read(io.BytesIO(data))
    assert rate == SAMPLE_RATE
    assert len(audio) == pytest.approx(SAMPLE_RATE * 0.2, rel=0.01)
    assert model.calls[0][0] == "属する"


@pytest.mark.asyncio
async def test_wav_is_pcm16_not_float(client) -> None:
    """float32 WAV 檔案大一倍，且不是每個瀏覽器都播得動。"""
    data = await client[0].synthesize("属する")

    assert sf.info(io.BytesIO(data)).subtype == "PCM_16"


@pytest.mark.asyncio
async def test_generation_parameters_come_from_settings(tmp_path: Path) -> None:
    """音色與生成參數不進簽章，一律由設定提供（約束 5）。"""
    ref = make_wav(tmp_path / "ref.wav")
    prompt = make_wav(tmp_path / "prompt.wav")
    settings = make_settings(
        tmp_path,
        reference_wav=ref,
        prompt_wav=prompt,
        prompt_text="逐字稿",
        cfg_value=3.5,
        inference_timesteps=20,
        normalize=True,
        denoise=True,
    )
    model = FakeTTSModel()

    await VoxCPMClient(settings, model_factory=lambda: model).synthesize("テスト")

    _, kwargs = model.calls[0]
    assert kwargs == {
        "prompt_wav_path": str(prompt),
        "prompt_text": "逐字稿",
        "reference_wav_path": str(ref),
        "cfg_value": 3.5,
        "inference_timesteps": 20,
        "normalize": True,
        "denoise": True,
    }


@pytest.mark.asyncio
async def test_unset_voice_paths_are_passed_as_none(client) -> None:
    voice, model = client

    await voice.synthesize("テスト")

    _, kwargs = model.calls[0]
    assert kwargs["reference_wav_path"] is None
    assert kwargs["prompt_wav_path"] is None
    assert kwargs["prompt_text"] is None


@pytest.mark.asyncio
async def test_no_retry_is_added_on_top(client) -> None:
    """套件內建 retry_badcase，本專案不再包一層——失敗就是一次呼叫。"""
    voice, model = client
    model.error = RuntimeError("CUDA out of memory")

    with pytest.raises(ExternalServiceError):
        await voice.synthesize("テスト")

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_package_errors_become_external_service_error(client) -> None:
    voice, model = client
    model.error = RuntimeError("CUDA out of memory")

    with pytest.raises(ExternalServiceError) as exc:
        await voice.synthesize("属する")

    message = str(exc.value)
    assert "CUDA out of memory" in message
    assert "RuntimeError" in message
    assert "属する" in message


@pytest.mark.asyncio
async def test_empty_text_is_rejected(client) -> None:
    voice, model = client

    with pytest.raises(ExternalServiceError, match="文字為空"):
        await voice.synthesize("   ")

    assert model.calls == []


@pytest.mark.asyncio
async def test_missing_sample_rate_is_reported(tmp_path: Path) -> None:
    model = FakeTTSModel()
    del model.tts_model

    with pytest.raises(ExternalServiceError, match="取樣率"):
        await VoxCPMClient(make_settings(tmp_path), model_factory=lambda: model).synthesize("x")


# ── 模型快取 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_is_built_only_once(tmp_path: Path) -> None:
    """建構要 77 秒、佔 6.3 GB VRAM，每次呼叫重建等於災難。"""
    builds = 0

    def factory() -> FakeTTSModel:
        nonlocal builds
        builds += 1
        return FakeTTSModel()

    voice = VoxCPMClient(make_settings(tmp_path), model_factory=factory)
    for _ in range(3):
        await voice.synthesize("テスト")

    assert builds == 1


@pytest.mark.asyncio
async def test_concurrent_first_calls_build_once(tmp_path: Path) -> None:
    """併發設 1 是預設，但鎖不能靠設定來保證。"""
    import asyncio

    builds = 0

    def factory() -> FakeTTSModel:
        nonlocal builds
        builds += 1
        return FakeTTSModel()

    voice = VoxCPMClient(make_settings(tmp_path), model_factory=factory)
    await asyncio.gather(*(voice.synthesize("テスト") for _ in range(4)))

    assert builds == 1


@pytest.mark.asyncio
async def test_configuration_error_surfaces_before_building(tmp_path: Path) -> None:
    """設定錯誤要在載入那 77 秒之前就擋下。"""
    settings = make_settings(tmp_path, model_path=tmp_path / "不存在")

    def factory() -> FakeTTSModel:  # pragma: no cover - 不該被呼叫
        raise AssertionError("設定錯誤時不該建構模型")

    with pytest.raises(ConfigurationError):
        await VoxCPMClient(settings, model_factory=factory).synthesize("テスト")


# ── 隨機音色警告 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_warns_once_when_no_voice_source(client, caplog) -> None:
    voice, _ = client

    with caplog.at_level("WARNING"):
        await voice.synthesize("一")
        await voice.synthesize("二")

    warnings = [r for r in caplog.records if "隨機音色" in r.message]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_no_warning_when_reference_wav_is_set(tmp_path: Path, caplog) -> None:
    settings = make_settings(tmp_path, reference_wav=make_wav(tmp_path / "ref.wav"))
    voice = VoxCPMClient(settings, model_factory=FakeTTSModel)

    with caplog.at_level("WARNING"):
        await voice.synthesize("テスト")

    assert [r for r in caplog.records if "隨機音色" in r.message] == []


# ── Voice Design ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_description_is_prepended(tmp_path: Path) -> None:
    """README 的格式：括號描述放在 text 開頭，後面直接接內容，中間不加空白。"""
    desc = "(A young woman, clear and steady voice)"
    model = FakeTTSModel()

    await VoxCPMClient(
        make_settings(tmp_path, voice_description=desc), model_factory=lambda: model
    ).synthesize("baby")

    assert model.calls[0][0] == f"{desc}baby"


@pytest.mark.asyncio
async def test_voice_description_is_trimmed(tmp_path: Path) -> None:
    model = FakeTTSModel()

    await VoxCPMClient(
        make_settings(tmp_path, voice_description="  (calm voice)  "),
        model_factory=lambda: model,
    ).synthesize("baby")

    assert model.calls[0][0] == "(calm voice)baby"


@pytest.mark.asyncio
async def test_no_description_leaves_text_untouched(client) -> None:
    voice, model = client

    await voice.synthesize("baby")

    assert model.calls[0][0] == "baby"


@pytest.mark.asyncio
async def test_description_and_reference_can_coexist(tmp_path: Path) -> None:
    """併用時描述退為風格控制（README 的 Controllable Voice Cloning）。"""
    ref = make_wav(tmp_path / "ref.wav")
    model = FakeTTSModel()

    await VoxCPMClient(
        make_settings(tmp_path, voice_description="(cheerful)", reference_wav=ref),
        model_factory=lambda: model,
    ).synthesize("baby")

    text, kwargs = model.calls[0]
    assert text == "(cheerful)baby"
    assert kwargs["reference_wav_path"] == str(ref)


@pytest.mark.asyncio
async def test_description_counts_as_a_voice_source(tmp_path: Path, caplog) -> None:
    """有描述就不是隨機音色，不該再警告。"""
    settings = make_settings(tmp_path, voice_description="(calm voice)")
    assert settings.uses_random_voice is False

    with caplog.at_level("WARNING"):
        await VoxCPMClient(settings, model_factory=FakeTTSModel).synthesize("baby")

    assert [r for r in caplog.records if "隨機音色" in r.message] == []
