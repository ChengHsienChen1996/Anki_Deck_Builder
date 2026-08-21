"""測試共用 fixtures。

各階段的 mock fixture 於對應 task 加入；此處只放全專案共用的路徑常數。
"""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """對齊樣板目錄（書頁照片、OCR 原文、理想抽取結果）。"""
    return FIXTURES_DIR


@pytest.fixture
def expected_cards_csv() -> Path:
    """黃金樣本 CSV，同時是 CardRow 欄位順序的驗證基準。"""
    return FIXTURES_DIR / "expected_cards.csv"
