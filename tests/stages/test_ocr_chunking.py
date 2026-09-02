"""ocr_chunking 單元測試。

**全部不需要真模型也不需要真影像**——本模組只吃偵測框的座標，
所以版面結構的推導可以完全用合成資料測（專案獨有測試規則：
付費／外部服務走 mock，純邏輯一律真測）。

核心不變式只有兩條，其餘都是它們的推論：
1. **聯集 ＝ 全頁**（不漏）
2. **互不重疊**（不重）
"""

from __future__ import annotations

import pytest

from anki_deck_builder.stages.ocr_chunking import (
    Box,
    detect_axis,
    infer_right_to_left,
    merge_to_budget,
    order,
    plan,
    split_to_budget,
    union_area_ratio,
)

PAGE = (3000, 4000)
HUGE = 10**9  # 大到不會觸發切割的預算


def boxes(*items: Box) -> list[dict]:
    return [{"cls": "plain text", "conf": 0.9, "box": list(b)} for b in items]


def area(b: Box) -> int:
    return (b[2] - b[0]) * (b[3] - b[1])


def assert_partitions(chunks: list[Box], size: tuple[int, int]) -> None:
    """核心不變式：聯集覆蓋全頁且互不重疊。

    以「面積總和 == 全頁面積」檢查——在互不重疊的前提下這等價於完整覆蓋，
    而互不重疊由下面的兩兩檢查保證。
    """
    assert sum(area(c) for c in chunks) == size[0] * size[1]
    for i, a in enumerate(chunks):
        for b in chunks[i + 1 :]:
            overlap_x = min(a[2], b[2]) - max(a[0], b[0])
            overlap_y = min(a[3], b[3]) - max(a[1], b[1])
            assert overlap_x <= 0 or overlap_y <= 0, f"{a} 與 {b} 重疊"


# ── 帶的排列方向 ─────────────────────────────────────────────────


def test_axis_is_x_when_boxes_form_columns() -> None:
    """兩欄之間有空隙 → 帶沿 x 並排。"""
    cols = boxes((100, 100, 1200, 3900), (1800, 100, 2900, 3900))
    axis, sep = detect_axis([tuple(b["box"]) for b in cols], PAGE)

    assert axis == "x"
    assert sep > 0


def test_axis_is_y_when_boxes_form_rows() -> None:
    rows = boxes((100, 100, 2900, 1200), (100, 1800, 2900, 2900))
    axis, _ = detect_axis([tuple(b["box"]) for b in rows], PAGE)

    assert axis == "y"


def test_axis_uses_gaps_not_aspect_ratio() -> None:
    """**不可以用長寬比判斷帶的方向。**

    這裡的框個個是「高 > 寬」的直長條，但它們沿 y 堆疊、沿 x 沒有空隙——
    正確答案是 y。用長寬比會答成 x。
    """
    tall_but_stacked = boxes(
        (100, 100, 900, 1400), (1000, 100, 1900, 1400), (2000, 100, 2900, 1400),
        (100, 2000, 900, 3300), (1000, 2000, 1900, 3300), (2000, 2000, 2900, 3300),
    )
    axis, _ = detect_axis([tuple(b["box"]) for b in tall_but_stacked], PAGE)

    assert axis == "y"


# ── 文字方向 ─────────────────────────────────────────────────────


def test_horizontal_text_reads_left_to_right() -> None:
    """框寬 > 高 ＝ 橫排文字 ＝ 欄序由左至右。

    實測四種素材分得乾乾淨淨（單字書 22/22 寬>高、繁中 A 0/26）。
    """
    wide = [(0, 0, 1000, 100), (0, 200, 1000, 300)]

    assert infer_right_to_left(wide) is False


def test_vertical_text_reads_right_to_left() -> None:
    tall = [(0, 0, 100, 1000), (200, 0, 300, 1000)]

    assert infer_right_to_left(tall) is True


def test_no_boxes_defaults_to_left_to_right() -> None:
    assert infer_right_to_left([]) is False


# ── 聯集面積 ─────────────────────────────────────────────────────


def test_union_does_not_double_count_overlaps() -> None:
    """**面積總和會因框重疊而重複計算**——實測 `zh_a` 的總和/全頁是 1.157。

    聯集必須 ≤ 1，否則拿來比較轉正方向會選錯邊。
    """
    stacked = [(0, 0, 1500, 2000)] * 5

    assert union_area_ratio(stacked, PAGE) == pytest.approx(0.25, abs=0.02)


# ── 排序 ─────────────────────────────────────────────────────────


def test_columns_order_right_to_left_for_vertical_text() -> None:
    cols: list[Box] = [(0, 0, 1000, 4000), (1000, 0, 2000, 4000), (2000, 0, 3000, 4000)]

    assert [c[0] for c in order(cols, "x", right_to_left=True)] == [2000, 1000, 0]


def test_columns_order_left_to_right_for_horizontal_text() -> None:
    cols: list[Box] = [(0, 0, 1000, 4000), (1000, 0, 2000, 4000)]

    assert [c[0] for c in order(cols, "x", right_to_left=False)] == [0, 1000]


def test_rows_always_order_top_down() -> None:
    rows: list[Box] = [(0, 2000, 3000, 4000), (0, 0, 3000, 2000)]

    assert [r[1] for r in order(rows, "y", right_to_left=True)] == [0, 2000]


# ── 預算：切開與合併 ─────────────────────────────────────────────


def test_oversized_band_is_split() -> None:
    band: Box = (0, 0, 3000, 4000)  # 12M px
    pieces = split_to_budget(band, [], budget_px=4_000_000, axis="x")

    assert len(pieces) >= 3
    assert all(area(p) <= 4_000_000 for p in pieces)


def test_split_recurses_until_every_piece_fits() -> None:
    """**一刀不夠**：空隙分佈不均時切出來的片段仍可能超標。

    實測 `zh_novel` 一刀之後還留下 7.5M 的塊（預算 4.0M）。
    """
    band: Box = (0, 0, 3000, 4000)
    uneven = [(0, 0, 3000, 200), (0, 3800, 3000, 4000)]  # 只有頭尾有框，中間一大片空白
    pieces = split_to_budget(band, uneven, budget_px=1_000_000, axis="x")

    assert all(area(p) <= 1_000_000 for p in pieces)


def test_merge_only_when_pieces_tile_exactly() -> None:
    """**外接框面積必須等於兩塊之和**，否則合併會吃進不屬於它們的區域。

    實測 24 頁有 5 頁覆蓋率 > 1.000 就是漏了這個檢查：帶被切開之後，
    「甲帶的下半」與「乙帶的上半」相鄰，合併它們的外接框會同時蓋住
    「甲帶的上半」與「乙帶的下半」。
    """
    top_of_left: Box = (0, 0, 1500, 2000)
    top_of_right: Box = (1500, 0, 3000, 2000)
    diagonal: Box = (1500, 2000, 3000, 4000)

    assert len(merge_to_budget([top_of_left, top_of_right], HUGE)) == 1
    assert len(merge_to_budget([top_of_left, diagonal], HUGE)) == 2


def test_merge_respects_the_budget() -> None:
    halves: list[Box] = [(0, 0, 3000, 2000), (0, 2000, 3000, 4000)]

    assert len(merge_to_budget(halves, budget_px=6_000_000)) == 2


# ── 完整流程 ─────────────────────────────────────────────────────


def test_plan_partitions_a_two_column_page() -> None:
    two_cols = boxes((100, 100, 1300, 3900), (1700, 100, 2900, 3900))
    _, chunks = plan(two_cols, PAGE, budget_px=HUGE)

    assert_partitions(chunks, PAGE)


def test_plan_partitions_and_respects_budget() -> None:
    two_cols = boxes((100, 100, 1300, 3900), (1700, 100, 2900, 3900))
    _, chunks = plan(two_cols, PAGE, budget_px=2_000_000)

    assert_partitions(chunks, PAGE)
    assert all(area(c) <= 2_000_000 for c in chunks)


def test_plan_falls_back_to_whole_page_without_detections() -> None:
    """偵測不到任何文字框時退回整頁——**不能回空清單**，那等於丟掉整頁。"""
    _, chunks = plan([], PAGE, budget_px=HUGE)

    assert chunks == [(0, 0, *PAGE)]


def test_plan_ignores_non_text_classes() -> None:
    """`figure`／`abandon` 是誤判的大宗，拿來推結構會歪掉。"""
    mixed = boxes((100, 100, 1300, 3900), (1700, 100, 2900, 3900))
    mixed.append({"cls": "figure", "conf": 0.3, "box": [0, 0, 3000, 4000]})

    layout, chunks = plan(mixed, PAGE, budget_px=HUGE)

    assert layout.axis == "x"
    assert_partitions(chunks, PAGE)
