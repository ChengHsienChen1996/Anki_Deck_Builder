"""影像編碼與縮圖單元測試（純本地運算，無外部服務，AI 執行至通過）。

`ocr_client` 的其餘部分需真實 agent，測試骨架在 `test_ocr_client.py` 且標記 manual；
這一段不呼叫任何服務，照常自動執行。
"""

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from anki_deck_builder.clients.ocr_client import (
    MAX_PIXELS,
    PIXEL_SAFETY_RATIO,
    build_image_input,
    encode_image_b64,
)
from anki_deck_builder.exceptions import WorkFileError


def _write_image(path: Path, size: tuple[int, int], fmt: str = "JPEG") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 180, 160)).save(path, format=fmt)
    return path


def _decode_size(b64: str) -> tuple[int, int]:
    with Image.open(io.BytesIO(base64.b64decode(b64))) as image:
        return image.size


@pytest.mark.asyncio
async def test_small_image_is_passed_through_untouched(tmp_path: Path) -> None:
    """未超標的影像不重新編碼——重壓一次只會損失畫質。"""
    source = _write_image(tmp_path / "small.jpg", (800, 600))

    encoded = await encode_image_b64(source)

    assert base64.b64decode(encoded) == source.read_bytes()


@pytest.mark.asyncio
async def test_oversized_image_is_downscaled_within_budget(tmp_path: Path) -> None:
    """4000x3000 = 12M px 是 tests/fixtures/pages 的真實尺寸，超過 GLM-OCR 上限。"""
    source = _write_image(tmp_path / "big.jpg", (4000, 3000))

    encoded = await encode_image_b64(source)

    width, height = _decode_size(encoded)
    assert width * height <= MAX_PIXELS * PIXEL_SAFETY_RATIO
    assert width * height > 0


@pytest.mark.asyncio
async def test_downscale_preserves_aspect_ratio(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "big.jpg", (4000, 3000))

    width, height = _decode_size(await encode_image_b64(source))

    assert width / height == pytest.approx(4 / 3, rel=0.01)


@pytest.mark.asyncio
async def test_custom_max_pixels_is_honoured(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "medium.jpg", (2000, 1500))

    width, height = _decode_size(await encode_image_b64(source, max_pixels=100_000))

    assert width * height <= 100_000 * PIXEL_SAFETY_RATIO


@pytest.mark.asyncio
async def test_png_is_accepted(tmp_path: Path) -> None:
    """PDF 渲染的產物是 PNG，必須吃得下。"""
    source = _write_image(tmp_path / "page.png", (600, 800), fmt="PNG")

    assert base64.b64decode(await encode_image_b64(source))


@pytest.mark.asyncio
async def test_encoded_output_has_no_data_prefix(tmp_path: Path) -> None:
    """Protocol 約定傳純 base64，data: 前綴由 build_image_input 補上。"""
    source = _write_image(tmp_path / "small.jpg", (100, 100))

    encoded = await encode_image_b64(source)

    assert not encoded.startswith("data:")
    assert build_image_input(encoded)[0]["content"][0]["image_url"].startswith("data:image/jpeg")


@pytest.mark.asyncio
async def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkFileError, match="不存在"):
        await encode_image_b64(tmp_path / "nope.jpg")


@pytest.mark.asyncio
async def test_empty_file_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")

    with pytest.raises(WorkFileError, match="空檔案"):
        await encode_image_b64(empty)


@pytest.mark.asyncio
async def test_non_image_raises(tmp_path: Path) -> None:
    """損毀的圖要在編碼階段就擋下，不要送出去讓遠端回一個難解的錯誤。"""
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image at all")

    with pytest.raises(WorkFileError, match="無法辨識的影像格式"):
        await encode_image_b64(broken)


@pytest.mark.asyncio
async def test_accepts_str_path(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "small.jpg", (100, 100))

    assert await encode_image_b64(str(source))
