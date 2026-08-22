"""PDF 渲染單元測試（純本地運算，無外部服務，AI 執行至通過）。

測試用 PDF 於執行時以 PIL 即時產生，不放入版控（見 phase-2-ocr.md Task 2.2）。
"""

from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from anki_deck_builder.exceptions import StageProcessingError
from anki_deck_builder.stages.pdf_render import (
    DEFAULT_DPI,
    PDF_UNIT_INCHES,
    page_filename,
    render_pdf,
)


def _all_exist(paths: list[str]) -> bool:
    """同步 helper：async 測試中不直接呼叫 pathlib（ruff ASYNC240）。"""
    return all(Path(item).is_file() for item in paths)


def _make_pdf(path: Path, pages: int = 3, size: tuple[int, int] = (620, 877)) -> Path:
    """以 PIL 產生多頁 PDF；每頁畫上不同灰階以便辨識頁序。

    620x877 約為 A4 @ 75 DPI，讓渲染出的像素數在測試中維持在小尺寸。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    images = [Image.new("RGB", size, (250 - index * 20,) * 3) for index in range(pages)]
    images[0].save(path, save_all=True, append_images=images[1:])
    return path


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return _make_pdf(tmp_path / "book.pdf")


# ── 正常路徑 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_renders_every_page(pdf: Path, tmp_path: Path) -> None:
    rendered = await render_pdf(pdf, tmp_path / "pdf_pages")
    assert len(rendered) == 3
    assert _all_exist([item for _, item in rendered])


@pytest.mark.asyncio
async def test_page_numbers_start_at_one_and_increment(pdf: Path, tmp_path: Path) -> None:
    rendered = await render_pdf(pdf, tmp_path / "pdf_pages")
    assert [number for number, _ in rendered] == [1, 2, 3]


@pytest.mark.asyncio
async def test_filenames_are_zero_padded(pdf: Path, tmp_path: Path) -> None:
    rendered = await render_pdf(pdf, tmp_path / "pdf_pages")
    assert [Path(item).name for _, item in rendered] == [
        "page_0001.png",
        "page_0002.png",
        "page_0003.png",
    ]


@pytest.mark.asyncio
async def test_zero_padding_makes_lexical_order_equal_page_order(
    tmp_path: Path,
) -> None:
    """零填充的目的：字典序 == 頁序，下游不必再做自然排序。"""
    pdf = _make_pdf(tmp_path / "long.pdf", pages=11)
    rendered = await render_pdf(pdf, tmp_path / "pdf_pages")
    names = [Path(item).name for _, item in rendered]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_creates_output_dir(pdf: Path, tmp_path: Path) -> None:
    output = tmp_path / "deep" / "pdf_pages"
    assert not output.exists()
    await render_pdf(pdf, output)
    assert output.is_dir()


@pytest.mark.asyncio
async def test_output_is_readable_png(pdf: Path, tmp_path: Path) -> None:
    rendered = await render_pdf(pdf, tmp_path / "pdf_pages")
    with Image.open(rendered[0][1]) as image:
        assert image.format == "PNG"
        assert image.size[0] > 0


@pytest.mark.asyncio
async def test_pages_are_rendered_in_order(pdf: Path, tmp_path: Path) -> None:
    """每頁灰階不同（250, 230, 210），確認頁碼沒有錯置。"""
    rendered = await render_pdf(pdf, tmp_path / "pdf_pages")
    greys = []
    for _, item in rendered:
        with Image.open(item) as image:
            greys.append(image.convert("RGB").getpixel((image.width // 2, image.height // 2)))
    assert greys[0] > greys[1] > greys[2]


@pytest.mark.asyncio
async def test_accepts_str_paths(pdf: Path, tmp_path: Path) -> None:
    rendered = await render_pdf(str(pdf), str(tmp_path / "pdf_pages"))
    assert len(rendered) == 3


# ── DPI ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_higher_dpi_yields_more_pixels(pdf: Path, tmp_path: Path) -> None:
    low = await render_pdf(pdf, tmp_path / "low", dpi=72)
    high = await render_pdf(pdf, tmp_path / "high", dpi=144)
    with Image.open(low[0][1]) as small, Image.open(high[0][1]) as large:
        assert large.width > small.width
        assert large.width == pytest.approx(small.width * 2, abs=2)


@pytest.mark.asyncio
async def test_dpi_maps_to_pdfium_scale(pdf: Path, tmp_path: Path) -> None:
    """DPI → scale 的換算是 dpi * 1/72，錯了會整批解析度不對。"""
    rendered = await render_pdf(pdf, tmp_path / "pdf_pages", dpi=DEFAULT_DPI)
    document = pdfium.PdfDocument(pdf)
    try:
        canvas_width = document.get_page_size(0)[0]
    finally:
        document.close()
    expected = round(canvas_width * DEFAULT_DPI * PDF_UNIT_INCHES)
    with Image.open(rendered[0][1]) as image:
        assert image.width == pytest.approx(expected, abs=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("dpi", [0, -100])
async def test_non_positive_dpi_raises(pdf: Path, tmp_path: Path, dpi: int) -> None:
    with pytest.raises(StageProcessingError, match="DPI"):
        await render_pdf(pdf, tmp_path / "pdf_pages", dpi=dpi)


# ── 錯誤情形 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_pdf_raises(tmp_path: Path) -> None:
    with pytest.raises(StageProcessingError, match="不存在"):
        await render_pdf(tmp_path / "nope.pdf", tmp_path / "pdf_pages")


@pytest.mark.asyncio
async def test_directory_as_input_raises(tmp_path: Path) -> None:
    with pytest.raises(StageProcessingError, match="不存在"):
        await render_pdf(tmp_path, tmp_path / "pdf_pages")


@pytest.mark.asyncio
async def test_corrupt_pdf_raises_project_error(tmp_path: Path) -> None:
    """pdfium 的例外要轉成專案例外，不外洩第三方型別（架構約束）。"""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nnot really a pdf")
    with pytest.raises(StageProcessingError, match="無法開啟 PDF"):
        await render_pdf(broken, tmp_path / "pdf_pages")


# ── 檔名輔助 ────────────────────────────────────────────────────


def test_page_filename() -> None:
    assert page_filename(1) == "page_0001.png"
    assert page_filename(1234) == "page_1234.png"
    assert page_filename(7, width=6) == "page_000007.png"
