"""媒體轉檔測試。

轉檔的目的是**體積**：記憶引擎會把 ZIP 內每個媒體檔解成 Blob 常駐記憶體，
PNG 與 WAV 的原始體積會直接決定它在手機上會不會被殺掉。
"""

from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf
from PIL import Image

from anki_deck_builder.config import MediaSettings
from anki_deck_builder.exceptions import StageProcessingError
from anki_deck_builder.stages.media_encode import encode_audio, encode_image


def png_bytes(size: tuple[int, int] = (320, 180)) -> bytes:
    """一張有漸層的 PNG——純色圖任何格式都會小到看不出差別。"""
    array = np.linspace(0, 255, size[0] * size[1] * 3, dtype=np.uint8)
    image = Image.fromarray(array.reshape((size[1], size[0], 3)))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def wav_bytes(seconds: float = 1.0, rate: int = 48000) -> bytes:
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, tone, rate, format="WAV")
    return buffer.getvalue()


# ── 影像 ─────────────────────────────────────────────────────────


def test_webp_is_much_smaller_and_still_decodes() -> None:
    source = png_bytes()

    data, extension = encode_image(source, MediaSettings(image_format="webp"))

    assert extension == "webp"
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    assert len(data) < len(source) / 2
    assert Image.open(io.BytesIO(data)).size == (320, 180)


def test_jpeg_uses_jpg_extension() -> None:
    """副檔名不是格式名的鏡射——`.jpeg` 在某些引擎的比對表裡對不上。"""
    data, extension = encode_image(png_bytes(), MediaSettings(image_format="jpeg"))

    assert extension == "jpg"
    assert data[:2] == b"\xff\xd8"


def test_png_passes_through_untouched() -> None:
    """設成 png 就是不轉檔——連重編碼都不做，避免無謂的畫質損耗。"""
    source = png_bytes()

    data, extension = encode_image(source, MediaSettings(image_format="png"))

    assert (data, extension) == (source, "png")


def test_image_quality_changes_the_size() -> None:
    small = encode_image(png_bytes(), MediaSettings(image_quality=40))[0]
    large = encode_image(png_bytes(), MediaSettings(image_quality=95))[0]

    assert len(small) < len(large)


def test_broken_image_fails_that_row_only() -> None:
    """約束 3：轉檔失敗是單列的事，訊息要說得出是哪個格式。"""
    with pytest.raises(StageProcessingError, match="webp"):
        encode_image(b"not an image at all", MediaSettings(image_format="webp"))


# ── 語音 ─────────────────────────────────────────────────────────


def test_mp3_is_much_smaller_and_still_decodes() -> None:
    source = wav_bytes()

    data, extension = encode_audio(source, MediaSettings(audio_format="mp3"))

    assert extension == "mp3"
    assert len(data) < len(source) / 4
    decoded, rate = sf.read(io.BytesIO(data))
    assert rate == 48000
    assert len(decoded) == pytest.approx(48000, rel=0.05)


def test_wav_passes_through_untouched() -> None:
    source = wav_bytes()

    assert encode_audio(source, MediaSettings(audio_format="wav")) == (source, "wav")


def test_compression_level_changes_the_size() -> None:
    small = encode_audio(wav_bytes(), MediaSettings(audio_compression=0.9))[0]
    large = encode_audio(wav_bytes(), MediaSettings(audio_compression=0.0))[0]

    assert len(small) < len(large)


def test_sample_rate_is_preserved() -> None:
    """取樣率沿用來源——VOXCPM2 輸出 48 kHz，降頻是額外的損失。"""
    data, _ = encode_audio(wav_bytes(rate=24000), MediaSettings())

    assert sf.info(io.BytesIO(data)).samplerate == 24000


def test_broken_audio_fails_that_row_only() -> None:
    with pytest.raises(StageProcessingError, match="mp3"):
        encode_audio(b"not audio", MediaSettings(audio_format="mp3"))
