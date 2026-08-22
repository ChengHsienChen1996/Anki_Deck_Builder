"""PDF 逐頁渲染為圖片（流程層）。

階段 ① 遇到 PDF 輸入時的前置步驟：把每一頁轉成圖片，再交給 OCR 逐張處理。
本模組不做 OCR，也不判別輸入類型（那是 `input_source` 的事）。

**檔名含零填充頁碼**（`page_0001.png`）是保序的關鍵：下游若以檔名排序，
零填充讓字典序等於頁序，不必再依賴自然排序。回傳值同時帶著明確的頁碼，
`ocr_source_page` 直接取用，不從檔名反推。

渲染以 `asyncio.to_thread` 包住——pdfium 是同步的 C 擴充，直接在 event loop 裡
跑整份 PDF 會把其他協程卡死。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pypdfium2 as pdfium

from ..exceptions import StageProcessingError

#: 預設渲染解析度。過低傷辨識率，過高則 base64 過大且逼近 OCR 模型的像素上限
#: （A4 @ 200 DPI 約 1654x2339 = 3.9M px，GLM-OCR 上限 9.63M px）
DEFAULT_DPI = 200

#: PDF canvas unit 的實際尺寸（1/72 英吋）。DPI 換算成 render() 的 scale 需乘上此值
PDF_UNIT_INCHES = 1 / 72

#: 渲染結果的存放子目錄（相對於 work_dir）
OUTPUT_SUBDIR = "pdf_pages"

#: 頁碼零填充的最小寬度
PAGE_NUMBER_WIDTH = 4


def page_filename(number: int, width: int = PAGE_NUMBER_WIDTH) -> str:
    """組出零填充的頁面檔名。"""
    return f"page_{number:0{width}d}.png"


def _render_sync(pdf_path: Path, output_dir: Path, dpi: int) -> list[tuple[int, str]]:
    """實際的渲染迴圈（同步，由 `render_pdf` 丟到執行緒跑）。

    檔案存在性也在此檢查——同步的檔案系統呼叫一律留在這個執行緒裡，
    不讓它們出現在 async 函式中（ruff ASYNC240）。
    """
    if not pdf_path.is_file():
        raise StageProcessingError(f"PDF 不存在：{pdf_path}")

    scale = dpi * PDF_UNIT_INCHES
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        document = pdfium.PdfDocument(pdf_path)
    except pdfium.PdfiumError as error:  # 密碼保護、檔案損毀
        raise StageProcessingError(f"無法開啟 PDF：{pdf_path}（{error}）") from error

    try:
        page_count = len(document)
        if page_count == 0:
            raise StageProcessingError(f"PDF 沒有任何頁面：{pdf_path}")

        # 超過四位數的 PDF 罕見，但寬度不夠會讓零填充失去保序作用
        width = max(PAGE_NUMBER_WIDTH, len(str(page_count)))
        rendered: list[tuple[int, str]] = []

        for index in range(page_count):
            number = index + 1
            page = document[index]
            try:
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    target = output_dir / page_filename(number, width)
                    image.save(target)
                finally:
                    bitmap.close()
            except pdfium.PdfiumError as error:
                raise StageProcessingError(
                    f"PDF 第 {number} 頁渲染失敗：{pdf_path}（{error}）"
                ) from error
            finally:
                page.close()
            rendered.append((number, str(target)))

        return rendered
    finally:
        document.close()


async def render_pdf(
    pdf_path: str | Path,
    output_dir: str | Path,
    dpi: int = DEFAULT_DPI,
) -> list[tuple[int, str]]:
    """把 PDF 逐頁渲染為 PNG。

    Args:
        pdf_path: 來源 PDF。
        output_dir: 輸出目錄，不存在會自動建立（慣例為 `work_dir / OUTPUT_SUBDIR`）。
        dpi: 渲染解析度，預設 `DEFAULT_DPI`。

    Returns:
        `[(頁碼, 圖片路徑), ...]`，頁碼由 1 起算並依序遞增。

    Raises:
        StageProcessingError: PDF 不存在、無法開啟、沒有頁面，或個別頁面渲染失敗。

    Note:
        整份 PDF 的任一頁渲染失敗即整體中止。這與「單頁 OCR 失敗要跳過」不同——
        渲染失敗代表檔案本身有問題，繼續跑只會得到一份殘缺的頁序。
    """
    if dpi <= 0:
        raise StageProcessingError(f"DPI 必須為正整數，收到 {dpi}")

    return await asyncio.to_thread(_render_sync, Path(pdf_path), Path(output_dir), dpi)
