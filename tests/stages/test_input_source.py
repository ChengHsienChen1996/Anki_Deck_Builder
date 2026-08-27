"""輸入源判別單元測試（純本地檔案操作，無外部相依，AI 執行至通過）。"""

from pathlib import Path

import pytest

from anki_deck_builder.exceptions import StageProcessingError
from anki_deck_builder.stages.input_source import (
    IMAGE_EXTENSIONS,
    InputKind,
    detect_input,
    is_image,
    list_images,
)


def _touch(path: Path, content: str = "x") -> Path:
    """建一個有內容的檔案；判別只看副檔名，內容不需為合法圖片。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── 單一檔案 ────────────────────────────────────────────────────


@pytest.mark.parametrize("suffix", sorted(IMAGE_EXTENSIONS))
def test_single_image_all_supported_extensions(tmp_path: Path, suffix: str) -> None:
    target = _touch(tmp_path / f"page{suffix}")
    assert detect_input(target) == (InputKind.IMAGE, [str(target)])


@pytest.mark.parametrize("suffix", [".pdf"])
def test_pdf(tmp_path: Path, suffix: str) -> None:
    target = _touch(tmp_path / f"book{suffix}")
    assert detect_input(target) == (InputKind.PDF, [str(target)])


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_text(tmp_path: Path, suffix: str) -> None:
    target = _touch(tmp_path / f"notes{suffix}")
    assert detect_input(target) == (InputKind.TEXT, [str(target)])


def test_extension_matching_is_case_insensitive(tmp_path: Path) -> None:
    target = _touch(tmp_path / "PAGE.JPG")
    kind, items = detect_input(target)
    assert kind is InputKind.IMAGE
    assert items == [str(target)]


def test_accepts_str_path(tmp_path: Path) -> None:
    """CLI 傳進來的是字串，不是 Path。"""
    target = _touch(tmp_path / "page.png")
    assert detect_input(str(target)) == (InputKind.IMAGE, [str(target)])


# ── 目錄 ────────────────────────────────────────────────────────


def test_image_dir(tmp_path: Path) -> None:
    for name in ("a.jpg", "b.png"):
        _touch(tmp_path / name)
    kind, items = detect_input(tmp_path)
    assert kind is InputKind.IMAGE_DIR
    assert [Path(item).name for item in items] == ["a.jpg", "b.png"]


def test_image_dir_natural_sort(tmp_path: Path) -> None:
    """字典序會排成 1, 10, 2——那會讓 ocr_source_page 整批錯位。"""
    for index in (1, 2, 10, 21, 3):
        _touch(tmp_path / f"page{index}.jpg")
    _, items = detect_input(tmp_path)
    assert [Path(item).name for item in items] == [
        "page1.jpg",
        "page2.jpg",
        "page3.jpg",
        "page10.jpg",
        "page21.jpg",
    ]


def test_natural_sort_handles_zero_padded_and_plain_mixed(tmp_path: Path) -> None:
    for name in ("page_0002.jpg", "page_10.jpg", "page_1.jpg"):
        _touch(tmp_path / name)
    _, items = detect_input(tmp_path)
    assert [Path(item).name for item in items] == [
        "page_1.jpg",
        "page_0002.jpg",
        "page_10.jpg",
    ]


def test_natural_sort_mixed_digit_and_alpha_names(tmp_path: Path) -> None:
    """數字段與文字段結構不同時不可拋 TypeError。"""
    for name in ("page2.jpg", "pageA.jpg", "cover.jpg", "10.jpg"):
        _touch(tmp_path / name)
    _, items = detect_input(tmp_path)
    assert len(items) == 4


def test_image_dir_ignores_non_images(tmp_path: Path) -> None:
    for name in ("page1.jpg", "notes.txt", "book.pdf", ".DS_Store"):
        _touch(tmp_path / name)
    _, items = detect_input(tmp_path)
    assert [Path(item).name for item in items] == ["page1.jpg"]


def test_image_dir_does_not_recurse(tmp_path: Path) -> None:
    _touch(tmp_path / "page1.jpg")
    _touch(tmp_path / "sub" / "page2.jpg")
    _, items = detect_input(tmp_path)
    assert [Path(item).name for item in items] == ["page1.jpg"]


def test_empty_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(StageProcessingError, match="沒有任何支援的圖片"):
        detect_input(tmp_path)


def test_dir_without_images_raises(tmp_path: Path) -> None:
    """只有 PDF 的目錄不算 IMAGE_DIR——PDF 要單獨指定，不然頁序無從保證。"""
    _touch(tmp_path / "book.pdf")
    with pytest.raises(StageProcessingError, match="沒有任何支援的圖片"):
        detect_input(tmp_path)


# ── 錯誤情形 ────────────────────────────────────────────────────


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(StageProcessingError, match="不存在"):
        detect_input(tmp_path / "nope.jpg")


def test_unsupported_extension_raises(tmp_path: Path) -> None:
    target = _touch(tmp_path / "scan.tiff")
    with pytest.raises(StageProcessingError) as excinfo:
        detect_input(target)
    message = str(excinfo.value)
    assert ".tiff" in message
    assert ".jpg" in message  # 訊息要說明支援哪些格式


def test_no_extension_raises(tmp_path: Path) -> None:
    target = _touch(tmp_path / "scan")
    with pytest.raises(StageProcessingError, match="無副檔名"):
        detect_input(target)


# ── 輔助函式 ────────────────────────────────────────────────────


def test_is_image(tmp_path: Path) -> None:
    assert is_image(tmp_path / "a.JPEG")
    assert not is_image(tmp_path / "a.txt")


def test_list_images_returns_paths(tmp_path: Path) -> None:
    _touch(tmp_path / "b.jpg")
    _touch(tmp_path / "a.jpg")
    assert [item.name for item in list_images(tmp_path)] == ["a.jpg", "b.jpg"]


def test_input_kind_is_str_enum() -> None:
    assert str(InputKind.IMAGE_DIR) == "image_dir"
