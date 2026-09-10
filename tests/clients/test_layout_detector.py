"""`auto` 轉正評分的單元測試（純本地運算，不載入偵測模型，AI 執行至通過）。

`LayoutDetector.detect()` 的其餘部分需要真的 DocLayout-YOLO 權重；
這裡只測**選方向的決策**，把 `_run()` 換成假的偵測結果即可覆蓋。

守的是 2026-09-10 修掉的回歸：舊評分用「文字框聯集面積」，而偵測失敗時
會吐一個佔全頁一半的低信心兜底框，於是失敗的分數贏過成功。
"""

from typing import Any

import pytest
from PIL import Image

from anki_deck_builder.clients.layout_detector import LayoutDetector, _beats
from anki_deck_builder.config import OCRChunkSettings

#: 直式頁面。轉 cw 之後寬高互換，`FakeDetector` 據此分辨自己拿到哪個方向
PORTRAIT = (400, 600)


def _boxes(specs: list[tuple[int, int, int, int]]) -> list[dict]:
    return [{"cls": "plain text", "conf": 0.9, "box": list(b)} for b in specs]


def _many_small_boxes(count: int) -> list[dict]:
    """貼合的文字框：數量多、聯集面積小。模擬方向正確時的偵測結果。"""
    return _boxes([(10, 10 + i * 12, 380, 18 + i * 12) for i in range(count)])


def _one_huge_box(size: tuple[int, int]) -> list[dict]:
    """佔全頁一半的兜底大框。模擬方向錯誤時偵測器崩掉的樣子。"""
    return _boxes([(0, 0, size[0], size[1] // 2)])


class _FakeDetector(LayoutDetector):
    """依影像尺寸決定回傳哪一組框，藉此模擬「兩個方向偵測品質不同」。"""

    def __init__(self, portrait: list[dict], landscape: list[dict]) -> None:
        super().__init__(OCRChunkSettings(enabled=True, page_rotation="auto"))
        self._portrait = portrait
        self._landscape = landscape

    def _run(self, image: Any) -> list[dict]:
        return self._portrait if image.size[1] > image.size[0] else self._landscape


def _pick(portrait: list[dict], landscape: list[dict]) -> tuple[int, int]:
    """跑一次 `_best_rotation`，回傳它選中的影像尺寸。"""
    base = Image.new("RGB", PORTRAIT, (255, 255, 255))
    image, _ = _FakeDetector(portrait, landscape)._best_rotation(base)
    return image.size


# ── _beats：兩個訊號的優先順序 ────────────────────────────────────


def test_count_wins_when_the_gap_is_large() -> None:
    """框數差距顯著時只看框數，面積再高也不算數。

    這就是 p35 的實況：0° 是 54 框／0.428，cw 是 1 框／0.537。
    """
    assert _beats((54, 0.428), (1, 0.537))
    assert not _beats((1, 0.537), (54, 0.428))


def test_area_breaks_the_tie_when_counts_are_close() -> None:
    """框數接近代表「框數說明不了什麼」，此時才由面積決定。"""
    assert _beats((50, 0.60), (48, 0.40))
    assert not _beats((50, 0.30), (48, 0.40))


def test_zero_boxes_never_beats_a_healthy_detection() -> None:
    """一個框都沒偵到的方向不該勝出，即使對手面積比是 0。"""
    assert not _beats((0, 0.0), (30, 0.0))


# ── _best_rotation：整合起來的選擇 ───────────────────────────────


def test_picks_the_orientation_with_more_text_boxes() -> None:
    """**這是被修掉的回歸本身。**

    直式偵到 40 個貼合小框（聯集面積小），橫式只偵到一個佔半頁的兜底框
    （聯集面積大）。舊評分會選橫式，把頁面切成穿過文字行的直條。
    """
    landscape_size = (PORTRAIT[1], PORTRAIT[0])
    assert _pick(_many_small_boxes(40), _one_huge_box(landscape_size)) == PORTRAIT


def test_picks_landscape_when_that_is_where_the_text_is() -> None:
    """反過來也要成立——修法不能退化成「永遠選原圖」。"""
    landscape_size = (PORTRAIT[1], PORTRAIT[0])
    assert _pick(_one_huge_box(PORTRAIT), _many_small_boxes(40)) == landscape_size


@pytest.mark.parametrize("count", [1, 3, 8])
def test_collapsed_detection_never_wins(count: int) -> None:
    """實測崩掉的方向是 0–8 框、健康的是 33–67 框，整個區間都不該勝出。"""
    landscape_size = (PORTRAIT[1], PORTRAIT[0])
    collapsed = _boxes([(0, 0, landscape_size[0], landscape_size[1] // 2)] * count)
    assert _pick(_many_small_boxes(33), collapsed) == PORTRAIT
