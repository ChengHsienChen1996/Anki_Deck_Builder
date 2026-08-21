"""prompt 檔案的一致性檢查。

prompt 內容本身由人工檢視（判準見 tests/fixtures/README.md），這裡只守住一件
機械性、但一旦漂移就會讓整批卡片視覺分裂的事：**統一風格後綴三處必須逐字相同**。

抽取模型只讀得到 `extract_cards.md`，所以後綴字串必須從
`image_prompt_template.md`（權威定義處）複製一份過去——複製就會有不同步的風險。
"""

import csv
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

STYLE_SUFFIX = (
    ", cinematic lighting, muted color palette, soft shadows, atmospheric, "
    "no text, no letters, no watermark"
)


def test_style_suffix_is_identical_in_both_prompt_files() -> None:
    for name in ("extract_cards.md", "image_prompt_template.md"):
        content = (PROMPTS_DIR / name).read_text(encoding="utf-8")
        assert STYLE_SUFFIX in content, f"{name} 的統一風格後綴與權威定義不符"


def test_golden_cards_use_the_same_suffix(expected_cards_csv: Path) -> None:
    """對齊樣板是 prompt 品質的判準，後綴不一致代表 prompt 已經偏離樣板。"""
    with expected_cards_csv.open(encoding="utf-8-sig", newline="") as fh:
        prompts = [row["image_prompt"] for row in csv.DictReader(fh)]

    assert prompts, "對齊樣板應有 image_prompt"
    for prompt in prompts:
        assert prompt.endswith(STYLE_SUFFIX.lstrip(", "))


def test_no_text_constraint_is_present() -> None:
    """記憶錨點圖不得含任何文字——這條約束不可被改掉。"""
    content = (PROMPTS_DIR / "extract_cards.md").read_text(encoding="utf-8")
    assert "no text" in content
    assert "不得出現任何文字" in content
