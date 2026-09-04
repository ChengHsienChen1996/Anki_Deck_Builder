"""核對檢查點：對照影像修正抽取結果（Phase 9 Task 9.6）。

分塊（9.3）與去重（9.4）把源頭修好之後，仍有三類殘留是**確定性規則做不到、
但看著影像可以判斷**的：

| 殘留 | 實測 | 為什麼規則做不到 |
|------|------|-----------------|
| 漏掉的條目 | 第 11 頁 16 → 14 | 條目落在被跳過或被丟棄的塊裡，程式無從得知「應該有而沒有」 |
| 字形錯誤 | `医療` 的 `いりよう`（小寫 ょ 變大寫） | OCR 的辨識錯誤，分塊改善不了 |
| 去重的平手 | `医療` 兩張完整度相同，程式保留先出現的**而它剛好是錯的** | 無從判斷哪一份對 |

## 為什麼不是 `BaseStage`

核對的單位是「一塊影像 → 動好幾張卡」，而 `BaseStage.process_row()` 的契約是
**只改自己那一列**。Task 9.6 的骨架探測實測了 9.0 原本設想的作法
（覆寫 `run()` 建索引、就地改兄弟列）：

    處理的來源列 ['p1']；result: succeeded=3, failed=0
    p1_001 reading='ORIG'  ← 改動沒進去
    p1_002 reading='ORIG'  ← 改動沒進去

**沒有例外、沒有警告、succeeded=3**——最壞的一種失敗。原因是 `BaseStage.run()`
一開始就 `rows = await store.read()`，而覆寫的 `run()` 為了建索引也讀了一次，
兩次讀出來是**不同的物件**（實測 `first[0] is second[0]` 為 False）。索引握的是
第一批，骨架寫回的是第二批。`image` 與 `audio` 也覆寫 `run()` 重讀一次，
但它們只拿來數進度條總數、不改內容，所以一直沒踩到。

因此核對比照 `pack` 做成**獨立子命令**：讀一次全表、套用、寫一次。
代價是沒有逐列狀態（無續作、Web UI 無單列重跑），換得不動骨架、不動
`STAGE_FIELDS`、不動 `CardRow` 欄位——加狀態欄位會讓現有 `cards.csv` 因
表頭嚴格比對而立刻讀不開，得再寫一支 migration。

## 設計依 Task 9.0 的四輪實測

見 [logs/2026-09-02_eval_ocr-tiling-and-verification.md]：

- **切塊送**，不是整頁——整頁時模型回報「影像上找不到這個詞條」而漏抓
- **一次只核對一個範圍**——專注時 `reading` 從 5 → 6 → **8** 筆
- **中性框架**，不說「這是 OCR 結果」——明說時模型過度更正（p5 從 7 筆暴增到 40 筆）
- **只自動套用 `reading`／`example`**——`front`／`hint`／`back` 的 26 筆提案僅約 3 筆
  正確，模型在那裡要做的是「判斷這是哪個詞條」，那正是它最容易錯的事

塊與 OCR 當初讀的**完全相同**（共用 `ocr_chunking.plan()` 與 `crop_to_b64()`）：
核對要看的就是那一塊。空白塊同樣跳過——沒有文字可核對，卻會讓模型憑空編造。
"""

from __future__ import annotations

import asyncio
import csv
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..clients.image_input import build_image_input, crop_to_b64
from ..clients.protocols import LLMClientProtocol
from ..config import Settings
from ..schemas import CardRow
from ..schemas.extract_output import (
    TRANSLATION_SEPARATORS,
    normalise_example_separator,
)
from ..schemas.verify_output import CORRECTABLE_FIELDS, VerifyOutput
from ..state import CardStore
from .ocr_chunking import TEXT_CLASSES, contains_text, plan

logger = logging.getLogger(__name__)

#: `agents.yaml` 中的核對 agent
VERIFY_AGENT = "VerifyAgent"

#: 核對範圍。**一次只送一個**——合併會讓專注度下降（9.0 第四輪：5 → 6 → 8 筆）。
#: 順序即執行順序，也是稽核檔裡的排序
SCOPES: tuple[str, ...] = ("reading", "example")

#: 提案被拒絕的理由代碼，寫進稽核檔的 `verdict` 欄
APPLIED = "applied"

#: 稽核檔的欄位。`before`／`after` 並排是為了能直接目視差異
_CORRECTION_COLUMNS = (
    "page", "chunk", "scope", "card_id", "field", "before", "after", "verdict", "reason",
)


@dataclass
class Proposal:
    """模型提出的一筆更正，加上本地的判定結果。

    **被拒絕的也保留**——稽核檔要能回答「模型提過什麼、為什麼沒套用」，
    而 `front`／`hint`／`back` 那類提案雖然不自動套用，裡面確實有真錯誤
    （`愈久→愈々`、`あくま→悪魔`），值得人工挑。
    """

    page: int | None
    chunk: int
    scope: str
    card_id: str
    field: str
    before: str
    after: str
    reason: str
    verdict: str

    @property
    def applied(self) -> bool:
        return self.verdict == APPLIED


@dataclass
class VerifyReport:
    """一次核對執行的結果。"""

    pages: int = 0
    calls: int = 0
    failed_calls: int = 0
    proposals: list[Proposal] = field(default_factory=list)
    corrections_path: Path | None = None

    @property
    def applied(self) -> int:
        return sum(1 for p in self.proposals if p.applied)

    @property
    def rejected(self) -> int:
        return len(self.proposals) - self.applied


def _card_list(cards: Sequence[CardRow], scope: str) -> str:
    """組出送給模型的卡片清單。

    只帶 `card_id`、`front` 與**本次範圍那一個欄位**。`front` 必須帶——
    prompt 明寫「清單給的 front 就是這張卡的詞條，當作既定事實」，
    不帶的話模型會回頭自己判斷詞條，那正是 9.0 第一輪連鎖誤判的來源。
    """
    lines = [f"card_id\tfront\t{scope}"]
    for card in cards:
        lines.append(f"{card.card_id}\t{card.front}\t{getattr(card, scope)}")
    return "\n".join(lines)


def _header(scope: str, cards: Sequence[CardRow]) -> str:
    """隨影像送出的文字。**中性框架**：不說這份清單是 OCR 的產物。

    9.0 第一輪實測明說時模型過度更正（p5 從 7 筆真實變動暴增到 40 筆）——
    與使用者原本的預期方向相反。
    """
    return (
        f"本次只核對 `{scope}` 這一個欄位，其他欄位一律不要回報。\n\n"
        f"卡片清單（以 tab 分隔）：\n{_card_list(cards, scope)}"
    )


def _differs_beyond_punctuation(before: str, after: str) -> bool:
    """兩個值除了標點以外是不是真的不同。

    9.0 第四輪：`example` 的 6 筆變動裡多數只差句末的「。」。那不是錯誤，
    套用只會製造雜訊與無謂的重跑。**只用於 `example`**——`reading` 的
    標點差異本身就可能是錯誤（`いりよう` 的小寫 ょ 不是標點，不受此影響）。
    """
    strip = str.maketrans("", "", "。、，,．.！!？?「」『』()（）　 \n\t")
    return before.translate(strip) != after.translate(strip)


def _translation_in_source_line(example: str) -> bool:
    """`example` 的第一行有沒有混進譯文。

    **實測補的，不是預防性程式碼**（2026-09-04，第 11 頁）：`example` 的 7 筆
    自動更正裡有 5 筆是這個形態——模型把書上的 `原文／譯文` 排版原樣抄進第一行，
    再把譯文接在第二行：

        衣服を整える。\n整理衣物。  →  衣服を整える／整裝。\n整理衣物。

    `normalise_example_separator()` 擋不住它：那個函式「只在沒有換行時才動手」，
    而這裡已經有換行了。後果不只是格式——**第一行正是 `tts_back_text` 的來源**
    （`derive_tts_back_text`），這串會被日文語音整句唸出來，後面接一段中文。

    修不如拒：把 `／` 兩側切開會猜錯哪半是原文（實測 `医療機関／医療機構` 兩半
    都是原文）。留在稽核檔裡供人工挑，比自動套用一個安靜的錯誤好。
    """
    head = example.strip().splitlines()[0] if example.strip() else ""
    return any(separator in head for separator in TRANSLATION_SEPARATORS)


def _judge(
    correction, scope: str, cards: dict[str, CardRow]
) -> tuple[str, str, str]:  # noqa: ANN001
    """判定一筆提案：回傳 `(verdict, before, after)`。

    `verdict` 為 `APPLIED` 表示可套用，其餘皆為拒絕理由（直接寫進稽核檔）。
    """
    card = cards.get(correction.card_id)
    if card is None:
        # 模型有時會回報別頁的卡，或把 card_id 拼錯
        return "unknown card_id", "", correction.value
    before = getattr(card, correction.field, "") if correction.field else ""
    after = correction.value.strip()

    if correction.field not in CORRECTABLE_FIELDS:
        # 白名單以外的欄位。**保留提案供人工挑**——那裡確實有真錯誤，
        # 只是模型的準確率低到不能自動套用（26 筆提案約 3 筆正確）
        return "field not correctable", before, after
    if correction.field != scope:
        # 本次只問一個範圍，答到別的欄位表示它沒有專注在被問的事情上
        return f"outside scope {scope}", before, after
    if correction.field == "example":
        after = normalise_example_separator(after)
    if not after:
        return "empty value", before, after
    if after == before:
        # prompt 明寫「不要回報與清單現值相同的更正」，但仍會發生。
        # **必須排在標點過濾之前**：完全相同的兩個字串在標點過濾眼中也「只差標點」，
        # 稽核檔會因此把「模型重複了現值」寫成「只差標點」，兩者該分開看
        return "no change", before, after
    if correction.field == "example":
        if not _differs_beyond_punctuation(before, after):
            return "punctuation only", before, after
        if _translation_in_source_line(after):
            return "translation in source line", before, after
    return APPLIED, before, after


def _resolve_conflicts(proposals: list[Proposal]) -> None:
    """同一張卡同一個欄位被不同塊提出**不同答案**時，兩筆都不套用。

    9.0 第三輪的發現：「塊間不一致是免費的信心指標——同一張卡兩塊給不同答案的，
    正好都是可疑的」。這裡把那個觀察變成規則。答案相同時只套一次
    （後續的標為 `duplicate`，不是衝突）。
    """
    seen: dict[tuple[str, str], list[Proposal]] = {}
    for proposal in proposals:
        if proposal.applied:
            seen.setdefault((proposal.card_id, proposal.field), []).append(proposal)

    for group in seen.values():
        values = {p.after for p in group}
        if len(values) > 1:
            for proposal in group:
                proposal.verdict = "conflicting chunks"
        else:
            for proposal in group[1:]:
                proposal.verdict = "duplicate"


async def _verify_chunk(
    client: LLMClientProtocol,
    image_b64: str,
    scope: str,
    cards: Sequence[CardRow],
) -> VerifyOutput:
    output = await client.run_agent(
        VERIFY_AGENT, build_image_input(image_b64, prompt=_header(scope, cards))
    )
    if not isinstance(output, VerifyOutput):
        raise TypeError(
            f"agent {VERIFY_AGENT!r} 回傳 {type(output).__name__}，預期 VerifyOutput"
            "（請確認 agents.yaml 的 output_schema）"
        )
    return output


async def verify(
    store: CardStore,
    client: LLMClientProtocol,
    detector: object,
    settings: Settings,
    pages: Sequence[int] | None = None,
    scopes: Sequence[str] = SCOPES,
    dry_run: bool = False,
    notify=print,  # noqa: ANN001 - 同 stages/vram.py 的作法
) -> VerifyReport:
    """對照影像核對卡片，套用白名單內的更正。

    Args:
        pages: 只核對這些 `ocr_source_page`。`None` 為全部。
        scopes: 要核對哪些範圍，一個範圍一次呼叫。
        dry_run: 只產出稽核檔，不動 `cards.csv`。

    Returns:
        本次的提案與套用統計。**單塊失敗不中斷**（約束 3 的精神）——
        核對是加分項，一塊讀不到不該讓整批白跑。
    """
    rows = await store.read()
    report = VerifyReport()

    sources = {r.ocr_source_page: r for r in rows if not r.card_id and r.source}
    by_page: dict[int | None, list[CardRow]] = {}
    for row in rows:
        if row.card_id:
            by_page.setdefault(row.ocr_source_page, []).append(row)

    budget = settings.ocr_chunk.budget_px
    for page, source in sorted(sources.items(), key=lambda kv: (kv[0] is None, kv[0])):
        cards = by_page.get(page, [])
        if not cards or (pages is not None and page not in pages):
            continue
        index = {card.card_id: card for card in cards}

        try:
            boxes, size = await asyncio.to_thread(detector.detect, source.source)
        except Exception as error:  # noqa: BLE001 - 偵測失敗跳過該頁，不中斷整批
            notify(f"  p{page}：版面偵測失敗，跳過（{error}）")
            continue

        _, chunks = plan(boxes, size, budget)
        text_boxes = [tuple(b["box"]) for b in boxes if b["cls"] in TEXT_CLASSES]
        wanted = [c for c in chunks if contains_text(c, text_boxes)]
        if not wanted:
            notify(f"  p{page}：沒有含文字的塊，跳過")
            continue

        crops = await asyncio.to_thread(crop_to_b64, source.source, size, wanted)
        report.pages += 1
        page_proposals: list[Proposal] = []

        for chunk_no, image_b64 in enumerate(crops, start=1):
            for scope in scopes:
                report.calls += 1
                try:
                    output = await _verify_chunk(client, image_b64, scope, cards)
                except Exception as error:  # noqa: BLE001 - 單塊失敗不中斷
                    report.failed_calls += 1
                    logger.warning("p%s 第 %s 塊 %s 核對失敗：%s", page, chunk_no, scope, error)
                    continue
                for correction in output.corrections:
                    verdict, before, after = _judge(correction, scope, index)
                    page_proposals.append(
                        Proposal(
                            page=page,
                            chunk=chunk_no,
                            scope=scope,
                            card_id=correction.card_id,
                            field=correction.field,
                            before=before,
                            after=after,
                            reason=correction.reason,
                            verdict=verdict,
                        )
                    )

        _resolve_conflicts(page_proposals)
        for proposal in page_proposals:
            if proposal.applied:
                setattr(index[proposal.card_id], proposal.field, proposal.after)
        report.proposals.extend(page_proposals)
        applied = sum(1 for p in page_proposals if p.applied)
        notify(f"  p{page}：{len(wanted)} 塊、{len(page_proposals)} 筆提案、套用 {applied}")

    report.corrections_path = await _write_corrections(store, report)
    if not dry_run and report.applied:
        await store.write(rows)
    return report


async def _write_corrections(store: CardStore, report: VerifyReport) -> Path | None:
    """把**全部**提案寫進稽核檔，含被拒絕的。

    這是還原的依據，也是 `front`／`hint`／`back` 那類「不自動套用但值得人工挑」
    的提案唯一的出口。沒有任何提案時不建檔。
    """
    if not report.proposals:
        return None
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = store.path.parent / f"verify-corrections-{stamp}.csv"

    def write_sync() -> None:
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(_CORRECTION_COLUMNS)
            for p in report.proposals:
                writer.writerow(
                    [
                        p.page, p.chunk, p.scope, p.card_id, p.field,
                        p.before, p.after, p.verdict, p.reason,
                    ]
                )

    await asyncio.to_thread(write_sync)
    return path
