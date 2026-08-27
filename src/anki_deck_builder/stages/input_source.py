"""輸入源判別（流程層）。

階段 ① 的第一步：認出使用者給的 `--input` 是什麼，並展開成待處理項目清單。
本模組**只做判別**，不讀圖、不轉檔、不呼叫任何外部服務。

四種輸入類型（見 .agent/plans/phase-2-ocr.md Task 2.1）：

| 輸入 | 類型 | 回傳清單 |
|------|------|----------|
| 單一圖片檔 | `IMAGE` | 該檔案 |
| 目錄且內含圖片 | `IMAGE_DIR` | 目錄下的圖片，**自然排序** |
| PDF 檔 | `PDF` | 該檔案（轉圖是 `pdf_render` 的事） |
| `.txt` / `.md` | `TEXT` | 該檔案（純文字繞過 OCR） |

自然排序是必要的而非美觀考量：字典序會把 `page10.jpg` 排在 `page2.jpg` 前面，
書頁順序一錯，`ocr_source_page` 就跟著錯，後續無從追溯。
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from ..exceptions import StageProcessingError

#: 支援的圖片副檔名（比對前一律轉小寫）
IMAGE_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})

#: 支援的 PDF 副檔名
PDF_EXTENSIONS: frozenset[str] = frozenset({".pdf"})

#: 支援的純文字副檔名
TEXT_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md"})


class InputKind(StrEnum):
    """輸入類型。

    與 `StageStatus` 同樣採 `StrEnum`，`str(kind)` 直接得到 `"image_dir"`，
    寫進日誌與錯誤訊息時不必再轉換。
    """

    IMAGE = "image"
    IMAGE_DIR = "image_dir"
    PDF = "pdf"
    TEXT = "text"


def _supported_formats_hint() -> str:
    """組出錯誤訊息用的支援格式說明。"""
    return (
        f"圖片 {'／'.join(sorted(IMAGE_EXTENSIONS))}、"
        f"PDF {'／'.join(sorted(PDF_EXTENSIONS))}、"
        f"純文字 {'／'.join(sorted(TEXT_EXTENSIONS))}，"
        "或一個內含圖片的目錄"
    )


def _natural_key(name: str) -> tuple[tuple[int, int, str], ...]:
    """自然排序鍵：把檔名中的連續數字當成數值比較。

    `page2` < `page10`，而非字典序的 `page10` < `page2`。
    每段一律回傳三元組，避免 `int` 與 `str` 直接相比在段落結構不同時拋 `TypeError`
    （例：`page2.jpg` 與 `pageA.jpg`）。
    """
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part.lower())
        for part in re.split(r"(\d+)", name)
    )


def is_image(path: Path) -> bool:
    """副檔名是否為支援的圖片格式。"""
    return path.suffix.lower() in IMAGE_EXTENSIONS


def list_images(directory: Path) -> list[Path]:
    """列出目錄下的圖片，依檔名自然排序。

    只看第一層、只取檔案——子目錄與非圖片檔（如封面 PDF、`.DS_Store`）一律忽略，
    不遞迴。書頁掃描通常是平坦目錄，遞迴反而容易把不相干的圖一起吃進來。
    """
    images = [item for item in directory.iterdir() if item.is_file() and is_image(item)]
    return sorted(images, key=lambda item: _natural_key(item.name))


def detect_input(path: str | Path) -> tuple[InputKind, list[str]]:
    """判別輸入類型並展開為待處理項目清單。

    Args:
        path: `--input` 給的路徑，檔案或目錄皆可。

    Returns:
        `(類型, 項目路徑清單)`。單一檔案的清單長度為 1；`IMAGE_DIR` 為自然排序後的圖片。

    Raises:
        StageProcessingError: 路徑不存在、副檔名不支援，或目錄內沒有任何圖片。
    """
    target = Path(path)

    if not target.exists():
        raise StageProcessingError(f"輸入路徑不存在：{target}")

    if target.is_dir():
        images = list_images(target)
        if not images:
            raise StageProcessingError(
                f"目錄內沒有任何支援的圖片：{target}"
                f"（支援 {'／'.join(sorted(IMAGE_EXTENSIONS))}，不遞迴子目錄）"
            )
        return InputKind.IMAGE_DIR, [str(item) for item in images]

    if not target.is_file():
        raise StageProcessingError(
            f"輸入既不是檔案也不是目錄：{target}。支援 {_supported_formats_hint()}"
        )

    suffix = target.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return InputKind.IMAGE, [str(target)]
    if suffix in PDF_EXTENSIONS:
        return InputKind.PDF, [str(target)]
    if suffix in TEXT_EXTENSIONS:
        return InputKind.TEXT, [str(target)]

    raise StageProcessingError(
        f"不支援的輸入格式 {suffix or '（無副檔名）'}：{target}。支援 {_supported_formats_hint()}"
    )
