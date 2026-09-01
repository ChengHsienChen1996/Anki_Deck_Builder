"""prompt 檔案的一致性檢查。

prompt 內容本身由人工檢視（判準見 tests/fixtures/README.md），這裡只守機械性、
但一旦漂移就會壞掉的不變式。

Phase 8 之後守的東西換了一批：統一風格後綴不再由抽取模型輸出，
語義層（`image_scene.md`）與語法層（profile 的 prompt 檔）各管一段，
所以這裡改守**層次不要混回去**，以及兩份抽取 prompt 的欄位規則仍然一致。
"""

import csv
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

#: Phase 8 之前由抽取模型逐字輸出的統一風格後綴。**現在它不該再出現在抽取端**
LEGACY_STYLE_SUFFIX = (
    ", cinematic lighting, muted color palette, soft shadows, atmospheric, "
    "no text, no letters, no watermark"
)

#: 兩份抽取 prompt 中，〈欄位規則〉以下必須逐字一致（見該檔的維護注意）。
#: 唯一的合法差異是維護注意那行互指對方檔名
EXTRACT_PROMPTS = ("extract_cards.md", "extract_cards_vision.md")


def _field_rules(name: str) -> list[str]:
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    body = text[text.index("# 欄位規則") :]
    normalised = body.replace("`extract_cards_vision.md`", "<對方>").replace(
        "`extract_cards.md`", "<對方>"
    )
    return normalised.splitlines()


def test_extract_prompts_share_identical_field_rules() -> None:
    """`two_stage` 與 `vision_direct` 共用同一個 output_schema，欄位規則分歧會讓
    兩條路徑產出不同形狀的卡片。

    這條靠人力維持過，而且**失敗過**：2026-09-01 發現 vision 版缺了
    `tts_front_text` 的 IPA 規則（實測音標會生出幾乎無聲的音檔），
    已同步。改以機械檢查釘住。
    """
    text_rules, vision_rules = (_field_rules(name) for name in EXTRACT_PROMPTS)

    assert text_rules == vision_rules


# ── 層次分離（Phase 8）─────────────────────────────────────────────


def test_extract_prompts_do_not_mention_the_image_layers() -> None:
    """抽取端不再產出聯想圖的任何一層——留著規則只會讓模型多填一個沒人讀的欄位。"""
    for name in EXTRACT_PROMPTS:
        content = (PROMPTS_DIR / name).read_text(encoding="utf-8")
        assert "image_prompt" not in content, f"{name} 仍提到 image_prompt"
        assert LEGACY_STYLE_SUFFIX not in content, f"{name} 仍含統一風格後綴"


def test_scene_prompt_forbids_the_syntax_layer() -> None:
    """語義層必須明文禁止風格詞與觸發詞，否則換模型時會被重複帶進去。"""
    content = (PROMPTS_DIR / "image_scene.md").read_text(encoding="utf-8")

    for marker in ("cinematic lighting", "masterpiece", "no watermark"):
        assert marker in content, f"image_scene.md 應明文禁止 {marker!r}"
    assert "絕對不要" in content


def test_scene_prompt_keeps_the_no_text_prop_rule() -> None:
    """記憶錨點圖不得含文字。治本做法是不要求會帶字的道具——這條不可被改掉。"""
    content = (PROMPTS_DIR / "image_scene.md").read_text(encoding="utf-8")

    assert "避開會帶出文字的道具" in content
    for prop in ("screen", "calendar", "signboard", "chart"):
        assert prop in content, f"image_scene.md 的禁用道具表少了 {prop}"


# ── 對齊樣板 ─────────────────────────────────────────────────────


def test_golden_cards_still_carry_the_legacy_suffix(expected_cards_csv: Path) -> None:
    """對齊樣板是 Phase 8 之前產出的資料，`image_prompt` 仍是黏合後的完整字串。

    留著它是刻意的——`scripts/migrate-to-image-scene.py` 的切分邏輯以這個形狀
    為前提，樣板一改那支腳本就失去外部基準。
    """
    with expected_cards_csv.open(encoding="utf-8-sig", newline="") as fh:
        prompts = [row["image_prompt"] for row in csv.DictReader(fh)]

    assert prompts, "對齊樣板應有 image_prompt"
    for prompt in prompts:
        assert prompt.endswith(LEGACY_STYLE_SUFFIX.lstrip(", "))
