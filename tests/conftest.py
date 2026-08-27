"""測試共用 fixtures。

各階段的 mock fixture 於對應 task 加入；此處只放全專案共用的路徑常數。
"""

import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

#: 本專案的環境變數前綴。開發機的 `.env` 在 `agent_factory` 匯入時就被
#: python-dotenv 灌進 `os.environ`，`monkeypatch.chdir()` 擋不住——設定層讀不到
#: 檔案，卻讀得到那些已經進了行程的變數。所以測試一律按前綴掃，不逐一列舉：
#: 列舉漏過兩次（`VOXCPM2_VOICE_DESCRIPTION`、Phase 7 的 `COMFYUI_WIDTH_NODE_ID`），
#: 兩次都是「新增設定 → 忘了同步清單 → 開發機 .env 一改測試就紅」。
ENV_PREFIXES = ("COMFYUI_", "VOXCPM2_", "MODEL_UNLOAD_", "INGEST_")


def clear_project_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉所有本專案前綴的環境變數，讓測試看到的是設定層的預設值。"""
    for name in list(os.environ):
        if name.startswith(ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fixtures_dir() -> Path:
    """對齊樣板目錄（書頁照片、OCR 原文、理想抽取結果）。"""
    return FIXTURES_DIR


@pytest.fixture
def expected_cards_csv() -> Path:
    """黃金樣本 CSV，同時是 CardRow 欄位順序的驗證基準。"""
    return FIXTURES_DIR / "expected_cards.csv"


@pytest.fixture
def materials_dir(fixtures_dir: Path) -> Path:
    """教材形態樣本（索引式／非語言領域／有釋義的非日文）。

    見 tests/fixtures/README.md〈教材形態〉——只用日文詞條頁測會高估品質。
    """
    return fixtures_dir / "materials"
