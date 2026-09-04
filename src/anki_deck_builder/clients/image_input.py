"""影像輸入的共用工具（服務層）。

`two_stage` 的 OCR 與 `vision_direct` 的抽取都要把影像送進 agent_factory，
編碼與縮圖的規則對兩條路徑完全相同，因此集中在此，不各寫一份。

## 編碼前必須縮圖

GLM-OCR 宣告的 `max_pixels` 為 9,633,792。實測 `tests/fixtures/pages/` 的書頁照片為
4000x3000 = 12M px，**直接送會超標**；而 DPI 並不足以框住像素數——同樣 DPI 200，
真 A4 畫布渲染出 3.87M px，手機照片轉的 PDF 卻是 12M px（見執行計畫 §2.4）。
因此上限在這一層強制，而不是靠調 DPI。

## 影像以 base64 傳入

避開路徑中的非 ASCII 字元、空白與跨平台分隔符；且 agent_factory 只有拿到
data URL 才估得出影像 token（遠端 URL 一律保守高估）。
"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path

from ..exceptions import WorkFileError

#: GLM-OCR 宣告的像素上限（`glmocr.vision.max_pixels`）。留 5% 餘裕避免邊界爭議
MAX_PIXELS = 9_633_792
PIXEL_SAFETY_RATIO = 0.95

#: 縮圖後的重新編碼格式。JPEG 對書頁照片的體積優勢明顯，OCR 不需要無損
RESAMPLE_FORMAT = "JPEG"
RESAMPLE_QUALITY = 92

#: 送進 agent 的 data URL 預設 MIME。呼叫端若自行編碼 PNG 需一併指定
DEFAULT_MIME = "image/jpeg"


def _fit_pixels(raw: bytes, max_pixels: int) -> tuple[bytes, str]:
    """像素數超過上限時等比縮圖，否則原樣回傳。

    Pillow 已是 `voxcpm` 的既有相依，不算為此新增套件。
    """
    from PIL import Image, UnidentifiedImageError

    budget = int(max_pixels * PIXEL_SAFETY_RATIO)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if width * height <= budget:
                return raw, ""
            # 等比縮放：新邊長 = 原邊長 * sqrt(預算 / 目前像素數)
            ratio = (budget / (width * height)) ** 0.5
            target = (max(1, int(width * ratio)), max(1, int(height * ratio)))
            resized = image.convert("RGB")
            resized.thumbnail(target, Image.LANCZOS)
            buffer = io.BytesIO()
            resized.save(buffer, format=RESAMPLE_FORMAT, quality=RESAMPLE_QUALITY)
    except UnidentifiedImageError as error:
        raise WorkFileError(f"無法辨識的影像格式（{error}）") from error

    return buffer.getvalue(), DEFAULT_MIME


def _encode_sync(path: Path, max_pixels: int) -> str:
    """讀檔、必要時縮圖、轉 base64（同步，由 `encode_image_b64` 丟到執行緒跑）。"""
    if not path.is_file():
        raise WorkFileError(f"影像不存在：{path}")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise WorkFileError(f"影像讀取失敗：{path}（{error}）") from error

    if not raw:
        raise WorkFileError(f"影像是空檔案：{path}")

    try:
        fitted, _ = _fit_pixels(raw, max_pixels)
    except WorkFileError as error:
        raise WorkFileError(f"{error}：{path}") from error

    return base64.b64encode(fitted).decode("ascii")


async def encode_image_b64(path: str | Path, max_pixels: int = MAX_PIXELS) -> str:
    """把影像檔讀成 base64 字串，超過像素上限時先等比縮圖。

    Args:
        path: 影像路徑。
        max_pixels: 像素上限，預設為 GLM-OCR 的 `max_pixels`。

    Returns:
        base64 內容（**不含** `data:` 前綴，與 `OCRClientProtocol` 一致）。

    Raises:
        WorkFileError: 檔案不存在、讀取失敗、是空檔，或不是可辨識的影像。
    """
    return await asyncio.to_thread(_encode_sync, Path(path), max_pixels)


def crop_to_b64(
    image_path: str, size: tuple[int, int], chunks: list[tuple[int, int, int, int]]
) -> list[str]:
    """依分塊裁切並轉 base64（同步，由 `to_thread` 呼叫）。

    **依 `size` 決定要不要旋轉**：偵測器可能為了辨識而把頁面轉正，
    回傳的座標是轉正後的；這裡必須以同一個方向裁切，否則框全部對不上。

    Task 9.6 起有第二個呼叫端（核對階段），且**兩者必須切出同樣的塊**——
    核對要看的就是 OCR 當初讀的那一塊。原本住在 `stages/ocr.py`，
    移到這一層的理由與本模組 docstring 的第一句相同：不各寫一份。
    """
    import base64
    import io

    from PIL import Image

    with Image.open(image_path) as im:
        image = im.convert("RGB")
        if image.size != size:
            rotated = image.rotate(-90, expand=True)
            image = rotated if rotated.size == size else image.rotate(90, expand=True)

        out: list[str] = []
        for box in chunks:
            buffer = io.BytesIO()
            image.crop(box).save(buffer, format="JPEG", quality=92)
            out.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
    return out


def build_image_input(
    image_b64: str,
    mime: str = DEFAULT_MIME,
    prompt: str | None = None,
) -> list[dict[str, object]]:
    """組出 agent_factory 的多模態 input。

    格式取自 submodule 的參考實作 `src/agent_factory/tests/test_multimodal.py`，
    該測試用的正是本專案的 `glm-ocr-optimized:latest`：影像一則訊息，文字另一則。

    Args:
        image_b64: 影像 base64（不含 `data:` 前綴）。
        mime: data URL 的 MIME 型態。
        prompt: 隨影像送出的文字。**OCR 路徑一律留空**——GLM-OCR 是
            prompt-limited 模型，任務前綴由 `instruction_file_path` 帶入即可，
            多送一份自擬文字會讓它退化（實測）。`vision_direct` 的抽取則需要
            用它帶入任務參數（領域、牌組前綴、card_id 前綴）。
    """
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": f"data:{mime};base64,{image_b64}",
                }
            ],
        }
    ]
    if prompt:
        messages.append({"role": "user", "content": prompt})
    return messages
