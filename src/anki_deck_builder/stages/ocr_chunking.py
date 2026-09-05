"""OCR 分塊：由版面偵測框推導結構，再把整頁**完整分割**（流程層，純函式）。

## 為什麼要分塊

GLM-OCR 在整頁尺度下會跳過小字。實測同一頁（Task 9.0）：

| 送法 | furigana 命中 | 耗時 |
|------|--------------|------|
| 整頁（9.15M px） | 1 / 15 | 32 秒 |
| 2×2 切塊（每塊 4.0M px） | 8 / 15 | 約 10 秒 |

**是尺度問題，而且切塊還快三倍。** 全牌組的佐證：24 頁的 `raw_text` 裡讀音
幾乎不存在，卡片的 `reading` 只有 97/342 能在 OCR 輸出中找到——其餘是抽取模型
憑知識生成的，那正是 `あしおと`（應為 `あしあと`）這類錯誤的來源。

## 原則：偵測結果用來決定「怎麼切」，不是「切哪裡」

直接拿偵測框當裁切區域的話，漏掉的文字會永久遺失——實測單字書頁 16 個條目
只有 14 個拿到本文框（涵蓋率 87%）。改成「用框推結構、用結構切整頁」之後：

- 聯集必然覆蓋全頁，涵蓋率天然 100%，不依賴偵測的召回率
- 誤判的 `figure` 框、重疊框都不會造成內容遺失，最多讓切線位置不理想
- 偵測模型的角色從「決定內容」降級為「提供結構線索」，出錯的代價大幅降低

實測 27 張（24 頁單字書 ＋ 3 種其他版面）覆蓋率全部 1.000、零超標塊。

## 本模組不碰 IO、不碰模型

偵測器由 `clients/layout_detector.py` 提供，本模組只吃它的輸出。
因此全部可單元測試，不需要真模型也不需要真影像。

直接拿偵測框當裁切區域的話，漏掉的文字會永久遺失——實測單字書頁 16 個條目
只有 14 個拿到本文框（涵蓋率 87%）。改成「用框推結構、用結構切整頁」之後：

- 聯集必然覆蓋全頁，涵蓋率天然 100%，不依賴偵測的召回率
- 誤判的 figure 框、重疊框都不會造成內容遺失，最多讓切線位置不理想
- 偵測模型的角色從「決定內容」降級為「提供結構線索」，出錯的代價大幅降低

"""

from __future__ import annotations

from dataclasses import dataclass

#: 只有這些類別算「文字」。figure／abandon 是誤判的大宗，拿來推結構會歪掉
TEXT_CLASSES = frozenset({"plain text", "title", "figure_caption", "table", "table_caption"})

Box = tuple[int, int, int, int]  # x1, y1, x2, y2


@dataclass(frozen=True)
class Layout:
    """一頁的版面結構。"""

    #: **帶的排列方向**，不是文字方向。"x" ＝ 帶沿 x 並排（縱向的欄），
    #: "y" ＝ 帶沿 y 堆疊（橫向的列）。兩者常常一致，但不必然——
    #: 實測 `ja_article` 是「兩篇直排文章上下堆疊」，帶沿 y 而文字直排
    axis: str
    #: 主軸上的切點（含 0 與邊界），相鄰兩點構成一個帶
    cuts: tuple[int, ...]
    #: 分群清晰度，0～1。越高代表帶與帶之間分得越開
    separation: float


#: 算聯集面積時的網格解析度。**不是逐像素**——那對 12M px 的頁面太慢，
#: 而這個值只用來比較兩種轉正方向的優劣，粗一點完全夠
_UNION_GRID = 200


def union_area_ratio(boxes: list[Box], size: tuple[int, int]) -> float:
    """文字框的**聯集**面積佔全頁的比例。

    **不能用面積總和**：偵測框大量重疊，總和會重複計算——實測 `zh_a` 的
    總和/全頁是 1.157、`zh_novel` 1.125，都超過 1，拿來比較轉正方向會選錯邊。
    """
    if not boxes or size[0] <= 0 or size[1] <= 0:
        return 0.0
    grid = [[False] * _UNION_GRID for _ in range(_UNION_GRID)]
    for x1, y1, x2, y2 in boxes:
        cx1 = max(0, min(_UNION_GRID - 1, x1 * _UNION_GRID // size[0]))
        cx2 = max(0, min(_UNION_GRID - 1, x2 * _UNION_GRID // size[0]))
        cy1 = max(0, min(_UNION_GRID - 1, y1 * _UNION_GRID // size[1]))
        cy2 = max(0, min(_UNION_GRID - 1, y2 * _UNION_GRID // size[1]))
        for r in range(cy1, cy2 + 1):
            row = grid[r]
            for c in range(cx1, cx2 + 1):
                row[c] = True
    return sum(sum(r) for r in grid) / (_UNION_GRID * _UNION_GRID)


def infer_right_to_left(boxes: list[Box]) -> bool:
    """由文字框的寬高比推斷**文字方向**，再據以決定欄的閱讀順序。

    橫排文字 → 框寬 > 高 → 欄序由左至右；直排文字 → 框高 > 寬 → 欄序由右至左。

    > 這一段原本寫成「語言慣例，程式推不出來，要由呼叫端給」——**那句話是錯的**。
    > 實測四種素材分得乾乾淨淨：單字書 22/22 寬>高、日文文章 9/11、
    > 繁中 A 0/26、繁中小說 4/20。寬高比就是訊號。
    >
    > 注意這與 `detect_axis` 用的訊號不同：**帶的排列方向看空隙、
    > 文字方向看寬高比**。兩者不可互換——直排的長方塊與橫排的表格欄位形狀相近，
    > 拿寬高比去判帶的方向會誤判。
    """
    if not boxes:
        return False
    wide = sum(1 for x1, y1, x2, y2 in boxes if (x2 - x1) > (y2 - y1))
    return wide / len(boxes) <= 0.5


def _spans(boxes: list[Box], axis: str) -> list[tuple[int, int]]:
    i = 0 if axis == "x" else 1
    return sorted((b[i], b[i + 2]) for b in boxes)


def _coverage_profile(boxes: list[Box], axis: str, extent: int) -> list[int]:
    """主軸上每個位置被幾個框覆蓋。用來找「沒有文字」的空隙。"""
    profile = [0] * (extent + 1)
    for lo, hi in _spans(boxes, axis):
        for p in range(max(0, lo), min(extent, hi) + 1):
            profile[p] += 1
    return profile


def _gaps(profile: list[int], min_width: int) -> list[tuple[int, int]]:
    """profile 中連續為 0 且夠寬的區間。"""
    out: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(profile):
        if v == 0 and start is None:
            start = i
        elif v != 0 and start is not None:
            if i - start >= min_width:
                out.append((start, i))
            start = None
    if start is not None and len(profile) - start >= min_width:
        out.append((start, len(profile)))
    return out


def detect_axis(boxes: list[Box], size: tuple[int, int]) -> tuple[str, float]:
    """判斷主軸，回傳 (軸, 分群清晰度)。

    **不用長寬比判斷**——直排的長方塊與橫排的表格欄位形狀相近，會誤判。
    改看「中心點在哪個軸上分得比較開」：把框投影到各軸，量空隙總寬佔比，
    空隙比例高的那一軸就是帶與帶之間的分界方向。
    """
    scores: dict[str, float] = {}
    for axis, extent in (("x", size[0]), ("y", size[1])):
        profile = _coverage_profile(boxes, axis, extent)
        gap = sum(hi - lo for lo, hi in _gaps(profile, min_width=max(8, extent // 100)))
        scores[axis] = gap / extent if extent else 0.0
    axis = max(scores, key=lambda k: scores[k])
    return axis, round(scores[axis], 3)


def build_layout(boxes: list[Box], size: tuple[int, int]) -> Layout:
    """由文字框推出主軸與切點。切點取空隙的中線，**不是空隙的邊界**——
    切在中線上，兩側的文字都不會被邊界誤差切到。"""
    axis, sep = detect_axis(boxes, size)
    extent = size[0] if axis == "x" else size[1]
    profile = _coverage_profile(boxes, axis, extent)
    mids = [(lo + hi) // 2 for lo, hi in _gaps(profile, min_width=max(8, extent // 100))]
    cuts = sorted({0, *(m for m in mids if 0 < m < extent), extent})
    return Layout(axis=axis, cuts=tuple(cuts), separation=sep)


def bands(layout: Layout, size: tuple[int, int]) -> list[Box]:
    """把切點展開成覆蓋全頁的帶。**聯集 ＝ 全頁，且互不重疊。**"""
    out: list[Box] = []
    for lo, hi in zip(layout.cuts, layout.cuts[1:], strict=False):
        if layout.axis == "x":
            out.append((lo, 0, hi, size[1]))
        else:
            out.append((0, lo, size[0], hi))
    return out


def order(chunks: list[Box], axis: str, right_to_left: bool) -> list[Box]:
    """依閱讀順序排序。

    直排（axis="x"）：欄由右至左；橫排（axis="y"）：列由上至下。
    `right_to_left` 由呼叫端決定——那是語言慣例，不是版面能推出來的，
    寫死在這裡會把工具綁死在特定語言（約束：領域不預設）。
    """
    if axis == "x":
        return sorted(chunks, key=lambda b: -b[0] if right_to_left else b[0])
    return sorted(chunks, key=lambda b: b[1])


def _area(b: Box) -> int:
    return (b[2] - b[0]) * (b[3] - b[1])


def split_to_budget(chunk: Box, boxes: list[Box], budget_px: int, axis: str,
                    depth: int = 0) -> list[Box]:
    """單一塊超過預算時，沿**次軸**在文字空隙處再切開；沒有空隙就等分。

    這是 `merge_to_budget` 的對偶：合併處理「塊太碎」，本函式處理「帶太大」。
    少了它，一條沒有空隙的長欄會整條超出預算——實測 zh_novel 出現 8.9M 的塊
    （預算 4.0M），furigana 尺度的優勢就沒了。

    切點一樣取空隙中線；空隙不足時等分，因為**寧可切在不理想的位置，
    也不要讓塊大到讀不出小字**——切開的代價是碎片，超標的代價是讀不到。
    """
    if _area(chunk) <= budget_px:
        return [chunk]

    sub = "y" if axis == "x" else "x"
    if depth >= 4:
        # 遞迴深度用盡：強制等分。**收斂優先於切點品質**——
        # 切在不理想的位置只是碎片，塊超標則是讀不到小字
        lo, hi = (chunk[1], chunk[3]) if sub == "y" else (chunk[0], chunk[2])
        need = -(-_area(chunk) // budget_px)
        step = max(1, (hi - lo) // need)
        edges = [*range(lo, hi, step)][:need] + [hi]
        return [(chunk[0], a, chunk[2], b) if sub == "y" else (a, chunk[1], b, chunk[3])
                for a, b in zip(edges, edges[1:], strict=False) if a < b]

    inside = [b for b in boxes
              if b[0] >= chunk[0] and b[2] <= chunk[2] and b[1] >= chunk[1] and b[3] <= chunk[3]]
    lo, hi = (chunk[1], chunk[3]) if sub == "y" else (chunk[0], chunk[2])
    extent = hi - lo

    cuts: list[int] = []
    if inside:
        shifted = [(b[0] - chunk[0], b[1] - chunk[1], b[2] - chunk[0], b[3] - chunk[1])
                   for b in inside]
        profile = _coverage_profile(shifted, sub, extent)
        cuts = [lo + (g0 + g1) // 2
                for g0, g1 in _gaps(profile, min_width=max(8, extent // 100))]
        cuts = [c for c in cuts if lo < c < hi]

    need = -(-_area(chunk) // budget_px)  # 至少要切成幾塊
    if len(cuts) + 1 < need:
        # 空隙不夠，補等分切點
        step = extent // need
        cuts = sorted({*cuts, *(lo + step * i for i in range(1, need))})

    out: list[Box] = []
    edges = [lo, *cuts, hi]
    for a, b in zip(edges, edges[1:], strict=False):
        if a >= b:
            continue
        piece = (chunk[0], a, chunk[2], b) if sub == "y" else (a, chunk[1], b, chunk[3])
        # **必須遞迴**：空隙切點分佈不均時，切出來的片段仍可能超標
        # （實測 zh_novel 一刀之後還有 7.5M／預算 4.0M）。
        # 遞迴時交換軸，避免在同一軸上反覆找不到空隙而空轉
        if _area(piece) > budget_px and depth < 4:
            out.extend(split_to_budget(piece, boxes, budget_px, sub, depth + 1))
        else:
            out.append(piece)
    return out


def merge_to_budget(chunks: list[Box], budget_px: int) -> list[Box]:
    """依序貪婪合併相鄰塊，直到合併後的外接框超過像素預算。

    這是「資訊碎片化」的解法：同一段落的相鄰區塊盡量留在同一塊，
    只有真的放不下才切開。**切開處不重疊**——重複的文字比碎片更難清理。
    """
    if not chunks:
        return []
    out: list[Box] = []
    cur = chunks[0]
    for nxt in chunks[1:]:
        cand = (min(cur[0], nxt[0]), min(cur[1], nxt[1]),
                max(cur[2], nxt[2]), max(cur[3], nxt[3]))
        # **外接框的面積必須等於兩塊面積之和**，否則合併會吃進不屬於它們的區域，
        # 與其他塊重疊。實測 24 頁有 5 頁覆蓋率 > 1.000 就是漏了這個檢查：
        # 帶被切開之後，「甲帶的下半」與「乙帶的上半」相鄰，合併它們的外接框
        # 會同時蓋住「甲帶的上半」與「乙帶的下半」
        tiles_exactly = _area(cand) == _area(cur) + _area(nxt)
        if tiles_exactly and _area(cand) <= budget_px:
            cur = cand
        else:
            out.append(cur)
            cur = nxt
    out.append(cur)
    return out


def contains_text(chunk: Box, boxes: list[Box]) -> bool:
    """這一塊裡有沒有偵測到的文字框（有任何交集就算）。

    **完整覆蓋必然會產生空白塊**（書溝、頁緣、留白），而把空白塊送進 OCR
    不是無害的——實測 GLM-OCR 拿到看不出內容的影像會退化成無限重複，
    一頁因此吐出 36376 字的垃圾（`agents.yaml` 早就記載過這個失敗模式，
    `max_tokens` 只把單次損害封頂，塊數一多就累加）。

    跳過空白塊**不會漏內容**——那裡本來就沒有文字——所以「不漏」的保證仍然成立。
    """
    return any(
        min(chunk[2], b[2]) > max(chunk[0], b[0]) and min(chunk[3], b[3]) > max(chunk[1], b[1])
        for b in boxes
    )


#: 判定退化的唯一比例門檻：不重複行數 / 總行數。
#:
#: 訂得很低是刻意的——**只擋真正的無限重複**。實測退化輸出的比例接近 0.01
#: （同一句話重複數百次），而正常頁面即使含少量重複片段也在 0.5 以上。
#: 訂高會誤殺，而誤殺的代價是整塊文字消失
_MIN_UNIQUE_LINE_RATIO = 0.25

#: 行數太少時不判斷——樣本不足，比例不可靠
_MIN_LINES_TO_JUDGE = 12


def looks_degenerate(text: str) -> bool:
    """輸出是不是退化的重複。

    這是第二道防線：跳過空白塊（`contains_text`）治本，但**有內容的塊也可能退化**，
    所以產出端仍要檢查。判準只看行的重複率，不看長度——長度門檻要隨塊面積校準，
    而重複率不必。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < _MIN_LINES_TO_JUDGE:
        return False
    return len(set(lines)) / len(lines) < _MIN_UNIQUE_LINE_RATIO


def plan(boxes_raw: list[dict], size: tuple[int, int], budget_px: int,
         right_to_left: bool | None = None) -> tuple[Layout, list[Box]]:
    """完整流程：過濾 → 推結構 → 展開成帶 → 排序 → 切開超標 → 合併過碎。

    `right_to_left` 給 `None` 時由 `infer_right_to_left()` 從框的形狀推斷。
    """
    text = [tuple(b["box"]) for b in boxes_raw if b["cls"] in TEXT_CLASSES]
    if not text:
        return Layout("y", (0, size[1]), 0.0), [(0, 0, *size)]
    if right_to_left is None:
        right_to_left = infer_right_to_left(text)
    layout = build_layout(text, size)
    ordered = order(bands(layout, size), layout.axis, right_to_left)
    # 先把過大的帶切開，再合併過碎的——順序不能反：先合併會讓大帶更大
    split: list[Box] = []
    for band in ordered:
        split.extend(split_to_budget(band, text, budget_px, layout.axis))
    return layout, merge_to_budget(split, budget_px)
