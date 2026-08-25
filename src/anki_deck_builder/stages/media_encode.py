"""媒體輸出格式轉換（流程層）。

外部服務給什麼就是什麼：ComfyUI 回 PNG、VOXCPM2 回 WAV。兩者都是**未壓縮或
壓不動**的格式，而牌組是要整包丟進瀏覽器的——記憶引擎（`engines/anki_engine.html`）
用 JSZip 把 ZIP 內每個檔案都解成 Blob 再 `createObjectURL`，**整包媒體同時常駐
記憶體**，體積直接決定它在手機上會不會被系統殺掉。

實測（308 張卡、616 段語音）：

| 項目 | 原始 | 轉檔後 |
|------|------|--------|
| SDXL 聯想圖 | PNG 平均 1209 KB | WebP q92 約 69 KB（1/17） |
| 語音 | WAV 平均 200 KB | MP3 約 17 KB（1/10） |
| 整包 | 約 443 MB | **約 35 MB** |

WebP 在平塗插畫上幾乎無損（1:1 裁切看不出差別），MP3 對單聲道語音同理。
兩者都由**既有相依**完成：Pillow（gradio 帶入）與 soundfile（voxcpm 帶入），
**不需要 ffmpeg 或 pydub**。

格式與品質全部來自 `MediaSettings`（約束 5）；設成 `png` / `wav` 即原樣輸出，
轉檔這一層完全不介入。
"""

from __future__ import annotations

import io

from ..config import MediaSettings
from ..exceptions import StageProcessingError

#: 設定值 → (Pillow 的 format 名稱, 副檔名)
_IMAGE_FORMATS: dict[str, tuple[str, str]] = {
    "png": ("PNG", "png"),
    "webp": ("WEBP", "webp"),
    "jpeg": ("JPEG", "jpg"),
}

#: 設定值 → (soundfile 的 format 名稱, 副檔名)
_AUDIO_FORMATS: dict[str, tuple[str, str]] = {
    "wav": ("WAV", "wav"),
    "mp3": ("MP3", "mp3"),
}


def encode_image(png: bytes, settings: MediaSettings) -> tuple[bytes, str]:
    """把 ComfyUI 回傳的 PNG 轉成輸出格式，回傳 `(bytes, 副檔名)`。

    Raises:
        StageProcessingError: 影像解不開或編碼失敗（約束 3：只影響該列）。
    """
    target, extension = _IMAGE_FORMATS[settings.image_format]
    if settings.image_format == "png":
        return png, extension

    # 延後匯入：Pillow 只在真的要轉檔時才需要
    from PIL import Image

    try:
        with Image.open(io.BytesIO(png)) as image:
            # WebP 與 JPEG 都不吃調色盤／灰階以外的奇怪模式，統一轉 RGB。
            # 聯想圖沒有透明度需求，丟掉 alpha 不會有損失
            converted = image.convert("RGB")
            buffer = io.BytesIO()
            converted.save(buffer, format=target, quality=settings.image_quality)
    except Exception as error:  # noqa: BLE001 - Pillow 的例外型別隨格式而異
        raise StageProcessingError(
            f"影像轉檔失敗（{type(error).__name__}: {error}）："
            f"目標格式 {settings.image_format}"
        ) from error

    return buffer.getvalue(), extension


def encode_audio(wav: bytes, settings: MediaSettings) -> tuple[bytes, str]:
    """把 VOXCPM2 回傳的 WAV 轉成輸出格式，回傳 `(bytes, 副檔名)`。

    取樣率與聲道數一律沿用來源，只換容器與編碼。

    Raises:
        StageProcessingError: 音訊解不開或編碼失敗。
    """
    target, extension = _AUDIO_FORMATS[settings.audio_format]
    if settings.audio_format == "wav":
        return wav, extension

    import soundfile as sf

    try:
        audio, sample_rate = sf.read(io.BytesIO(wav), dtype="float32")
        buffer = io.BytesIO()
        sf.write(
            buffer,
            audio,
            sample_rate,
            format=target,
            compression_level=settings.audio_compression,
        )
    except Exception as error:  # noqa: BLE001 - soundfile 的例外型別隨後端而異
        raise StageProcessingError(
            f"語音轉檔失敗（{type(error).__name__}: {error}）："
            f"目標格式 {settings.audio_format}"
        ) from error

    data = buffer.getvalue()
    if not data:
        raise StageProcessingError(f"語音轉檔後內容為空：目標格式 {settings.audio_format}")
    return data, extension
