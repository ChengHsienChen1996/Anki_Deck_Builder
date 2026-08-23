"""階段 ②：LLM 抽取整理（流程層）。

讀入一列的 `raw_text`（通常是一整頁教材），呼叫 LLM 切出若干張卡片，
把 (a) 引擎欄位與 (b) 製卡中間欄位寫進**新增的列**，原 `raw_text` 列標為 `done` 保留。

**Phase 1 只走文字路徑**（`ExtractAgent`）。影像直送（`vision_direct` / `VisionExtractAgent`）
於 Phase 2 加入——切換點集中在 `_build_request()`，屆時只需補分支，
不必動 `process_row()` 或任何欄位套用邏輯。

## 已知限制：`--force` 重跑同一頁

重新抽取同一頁會產出與上次相同的 `card_id`，撞上唯一性檢查而讓該列失敗。
根本解法是「重跑時先移除該來源列上次產出的卡片列」，但骨架目前只支援
**新增**列、不支援移除列，改動骨架須先與使用者確認（見 architecture.md
〈擴充性設計〉的限制）。在那之前，重跑同一頁請先手動移除舊卡片列，
或改用不同的 `card_id` 前綴——失敗訊息會如此提示。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..clients.image_input import build_image_input, encode_image_b64
from ..clients.protocols import AgentInput, LLMClientProtocol
from ..config import Settings
from ..exceptions import StageProcessingError
from ..schemas import CardRow, ExtractedCard, ExtractOutput, StageStatus
from .base import BaseStage, register_stage

#: 文字路徑使用的 agent（`agents.yaml` 中的 name）
TEXT_AGENT = "ExtractAgent"
#: 每段的非空行數上限。真正的限制是「一次要產出幾張卡」，但那事前無從得知——
#: 教材密度差異極大（日文詞條一條約 4 行、英文單字表一條 1 行），因此以行數起步，
#: 失敗再對半切（見 `_extract_chunk`）
DEFAULT_CHUNK_LINES = 20

#: 對半切的下限。再小就不是「模型吃不下」而是別的問題，繼續切只是浪費呼叫
MIN_CHUNK_LINES = 4

#: 條目符號。只認「強標記」——`•`、`・` 在多數教材裡是例句或子項而非條目，
#: 把它們算進去會高估條目數、觸發不必要的重試
ENTRY_MARKER = re.compile(r"^\s*(?:[□■◆●▲☆★]|\d{1,3}\s*[.)、．])")

#: 估到的條目數少於此值就不做產出檢查——樣本太小，判斷不可靠
MIN_ENTRIES_TO_CHECK = 3


def estimate_entries(text: str) -> int:
    """從條目符號估算這段有幾個條目。

    估不出來（教材沒有符號，例如一行一詞的索引式單字表）時回 0，
    呼叫端據此跳過檢查——寧可不檢查，也不要用不可靠的估計去觸發重試。
    """
    return sum(1 for line in text.splitlines() if ENTRY_MARKER.match(line))


def _check_yield(chunk: str, cards: Sequence[ExtractedCard]) -> None:
    """產出明顯少於條目數時視為失敗，交給對半重試。

    **這是無聲失敗的防線**：模型偶爾會呼叫成功、JSON 合法，卻只回一張卡就收工
    （實測 7 個條目的段落回 1 張，23 秒交差）。沒有這道檢查，缺掉的卡片會直接
    寫進工作檔，使用者無從察覺。

    只在條目數估得出來且夠多時才檢查，門檻訂在「不到一半」——模型合併同源條目
    或略過頁首頁尾都算正常，不該為此重試。
    """
    expected = estimate_entries(chunk)
    if expected < MIN_ENTRIES_TO_CHECK or len(cards) * 2 >= expected:
        return
    raise StageProcessingError(
        f"產出 {len(cards)} 張卡，明顯少於這段估計的 {expected} 個條目"
    )


def split_into_chunks(text: str, max_lines: int) -> list[str]:
    """把教材本文切成數段，每段最多 `max_lines` 個非空行。

    盡量在空行處收尾——空行通常是條目之間的分界，從那裡切開比較不會把一個條目
    切成兩半。整段都放得下時回傳單一元素，行為與未切段時相同。
    """
    lines = text.splitlines()
    non_empty = sum(1 for line in lines if line.strip())
    if max_lines <= 0 or non_empty <= max_lines:
        return [text] if text.strip() else []

    chunks: list[str] = []
    buffer: list[str] = []
    count = 0
    for line in lines:
        buffer.append(line)
        if line.strip():
            count += 1
        # 額滿後等到空行才收尾，避免切斷條目；差距過大時才硬切
        if count >= max_lines and (not line.strip() or count >= max_lines * 2):
            chunks.append("\n".join(buffer))
            buffer, count = [], 0
    if buffer:
        chunks.append("\n".join(buffer))
    return [chunk for chunk in chunks if chunk.strip()]


#: 判斷教材是否已含釋義的 agent
DETECT_AGENT = "MaterialTypeAgent"

#: 判斷時送出的教材片段行數。判斷「有沒有釋義」不需要看完整頁，
#: 而短輸入讓這次呼叫的成本可忽略
DETECT_SAMPLE_LINES = 40

#: `MaterialTypeAgent` 回報「沒有釋義」時的關鍵字
NO_DEFINITIONS_MARK = "NO_DEFINITIONS"

#: 補釋義使用的 agent（索引式教材的前置步驟）
ENRICH_AGENT = "EnrichAgent"

#: 影像直送路徑使用的 agent（vision_direct）
VISION_AGENT = "VisionExtractAgent"


@register_stage("extract")
class ExtractStage(BaseStage):
    """把 `raw_text` 抽取成結構化卡片。"""

    def __init__(
        self,
        client: LLMClientProtocol,
        settings: Settings | None = None,
        deck_name: str | None = None,
        domain: str | None = None,
        source: str | None = None,
        card_id_prefix: str | None = None,
        card_language: str | None = None,
        deck_categories: str | None = None,
        enrich: bool | None = None,
    ) -> None:
        """
        Args:
            client: LLM client（依 Protocol 注入，測試以假實作替換）。
            settings: 併發上限與輸入模式的來源。`None` 時併發為 1、走文字路徑。
            deck_name: 牌組名稱前綴（CLI 的 `--deck-name`）。未指定時由 LLM 依內容判定。
            domain: 學習領域。未指定時由 LLM 依內容判定。
            source: 寫入 `source` 欄位的值，例如「單字書 p.333」。
            card_language: `back` 的書寫語言，例如「繁體中文」。教材本身沒有譯文時
                （純單字表、原文書），模型會跟著教材語言走；此參數把它拉回來。
            enrich: 是否走補釋義路徑。`None`（預設）為**自動判斷**——每列開工前先以
                `MaterialTypeAgent` 問一次「這份教材有沒有釋義」，回報沒有才補。
                `True`／`False` 為強制覆寫，自動判斷失準時可用。
                教材只有詞條、沒有釋義時需要它（索引頁、單字表）。開啟後每段先經
                `EnrichAgent` 補上釋義與例句，再交給抽取——實測 8B 模型在單次呼叫裡
                同時做「憑知識生成」與「結構化輸出」會顧此失彼，整頁 101 張卡中
                有連續三段完全沒補例句。教材有沒有釋義只有呼叫端知道，因此是參數
                而非自動偵測。
            deck_categories: `deck` 最後一層的可選分類，以 `／` 分隔。
                教材屬於哪個領域只有呼叫端知道，寫死進 prompt 會把工具綁死在
                語言類教材上——實測列舉清單能讓分類 98/98 正確，但同一份清單
                套到藥理學就讓模型退回單一泛稱桶。
            card_id_prefix: `card_id` 前綴。實際送出的前綴會再串上頁碼，
                因為每列是獨立一次呼叫，模型看不到別頁，不加頁碼會跨頁撞號。
        """
        self.client = client
        self.settings = settings
        self.deck_name = deck_name
        self.domain = domain
        self.source = source
        self.card_id_prefix = card_id_prefix
        self.card_language = card_language
        self.deck_categories = deck_categories
        self.enrich = enrich
        self.chunk_lines = settings.ingest.extract_chunk_lines if settings else DEFAULT_CHUNK_LINES
        # 併發固定 1。本地 31B 模型在單張 GPU 上是序列化執行，併發不會更快——
        # 更糟的是**逾時計時器在排隊時照樣在跑**：單頁抽取約 157s，四列並送時
        # 最後一列光排隊就超過 agents.yaml 的 timeout: 600 而全數失敗（實測）。
        # 序列送出則每列的計時從真正發出請求才開始。
        # 理由同 OCR 階段（執行計畫 §Q5）。
        self.concurrency = 1
        self._seen_ids: set[str] = set()

    async def run(
        self,
        store,  # noqa: ANN001 - 型別同 BaseStage.run
        force: bool = False,
        only_failed: bool = False,
    ):
        """先載入檔案中既有的 `card_id`，再交給骨架執行。

        唯一性必須跨整份工作檔成立，而 `process_row()` 只看得到自己那一列，
        所以在這裡先把既有的 id 收齊。
        """
        self._seen_ids = {row.card_id for row in await store.read() if row.card_id}
        return await super().run(store, force=force, only_failed=only_failed)

    async def _needs_enrichment(self, row: CardRow) -> bool:
        """這一列要不要先補釋義。

        以整列（而非每段）判斷一次：教材性質是整頁一致的，逐段問只是重複付費。
        判斷失敗時**保守地不補**——多花一次呼叫事小，把已有釋義的教材重寫一遍
        反而可能覆蓋掉原文。
        """
        if self.enrich is not None:
            return self.enrich

        sample = "\n".join(row.raw_text.splitlines()[:DETECT_SAMPLE_LINES])
        try:
            verdict = await self.client.run_agent(DETECT_AGENT, sample)
        except Exception:  # noqa: BLE001 - 判斷失敗不該讓整列失敗
            return False
        return NO_DEFINITIONS_MARK in str(verdict).upper()

    async def process_row(self, row: CardRow) -> Sequence[CardRow]:
        """處理一列，回傳它切出的卡片列。

        輸入可能是 `raw_text`（two_stage）或影像（vision_direct），
        由 `_build_request()` 決定。
        """
        if not row.raw_text.strip() and not self._is_vision_row(row):
            if row.card_id:
                # 這是先前抽取產出的卡片列，本階段對它已無事可做（--force 會走到這裡）
                return ()
            raise StageProcessingError("此列沒有 raw_text，也不是已抽取的卡片列")

        if self._is_vision_row(row) and not row.raw_text.strip():
            # 影像無法切段，整張送
            output = await self._call(VISION_AGENT, await self._vision_input(row))
            return self._to_rows(output.cards, row)

        cards: list[ExtractedCard] = []
        enrich = await self._needs_enrichment(row)
        chunks = split_into_chunks(row.raw_text, self.chunk_lines)
        for index, chunk in enumerate(chunks, start=1):
            label = f"b{index}" if len(chunks) > 1 else ""
            cards.extend(
                await self._extract_chunk(chunk, row, label, self.chunk_lines, enrich)
            )
        return self._to_rows(self._renumber(cards, row), row)

    async def _extract_chunk(
        self, chunk: str, row: CardRow, label: str, budget: int, enrich: bool
    ) -> list[ExtractedCard]:
        """抽取一段文字；失敗時對半再切重試。

        本地模型面對太多條目時**不會報錯，而是退化**——吐出壞掉的 JSON，或乾脆
        只回一張卡就收工（實測 94 個條目的頁面只回 1 張）。可行的條目數又隨教材
        格式而異（日文詞條一條佔 4 行、英文單字表一條佔 1 行），事前無從得知，
        因此改為「失敗就對半再切」，讓它自己收斂到可行的粒度。
        """
        header = self._compose_header(row, label)
        try:
            source = await self._enrich(chunk, header) if enrich else chunk
            output = await self._call(TEXT_AGENT, f"{header}\n\n{source}" if header else source)
            # 產出檢查一律對照**原始**輸入的條目數：補釋義那步若漏掉條目，
            # 拿補完的結果當基準就檢查不出來了
            _check_yield(chunk, output.cards)
        except StageProcessingError:
            halved = budget // 2
            if halved < MIN_CHUNK_LINES:
                raise
            parts = split_into_chunks(chunk, halved)
            if len(parts) < 2:
                raise
            cards: list[ExtractedCard] = []
            for index, part in enumerate(parts, start=1):
                cards.extend(
                    await self._extract_chunk(part, row, f"{label}{index}", halved, enrich)
                )
            return cards
        return list(output.cards)

    def _renumber(
        self, cards: Sequence[ExtractedCard], row: CardRow
    ) -> Sequence[ExtractedCard]:
        """有前綴可用時，`card_id` 一律由本階段重新編號。

        切段之後每段都是獨立呼叫、序號各自從 001 起算，唯一性若押在模型的服從度上
        就會出事——實測看過模型把段落標籤當成 id（`..._b1`、`..._b2`），而那些字串
        又正好是其他段的前綴。改由程式編號後，這一整類失敗直接消失。

        沒有前綴時維持原樣：那是 Phase 1 的行為（模型依領域與讀音自行取名）。
        """
        prefix = self._card_id_prefix_for(row)
        if not prefix:
            return cards
        return [
            card.model_copy(update={"card_id": f"{prefix}_{index:03d}"})
            for index, card in enumerate(cards, start=1)
        ]

    async def _enrich(self, chunk: str, header: str) -> str:
        """把只有詞條的清單補成有釋義的教材頁。

        回傳純文字（`EnrichAgent` 不設 `output_schema`）。補出來的東西太短就當作
        失敗——上層的對半重試會接手。
        """
        try:
            result = await self.client.run_agent(
                ENRICH_AGENT, f"{header}\n\n{chunk}" if header else chunk
            )
        except Exception as exc:  # noqa: BLE001 - 同 _call，一律轉譯讓重試接手
            raise StageProcessingError(
                f"補釋義失敗：{type(exc).__name__}: {exc}"
            ) from exc

        if not isinstance(result, str):
            raise StageProcessingError(
                f"agent {ENRICH_AGENT!r} 應回傳純文字，卻收到 {type(result).__name__}"
                "（請確認 agents.yaml 未替它宣告 output_schema）"
            )
        text = result
        if len(text.strip()) < len(chunk.strip()):
            raise StageProcessingError("補釋義的產出比原文還短，判定為失敗")
        return text

    async def _call(self, agent_name: str, input_: AgentInput) -> ExtractOutput:
        """送出一次呼叫並確認型別。

        **任何**呼叫失敗都轉成 `StageProcessingError`，讓上層的對半重試接得住——
        模型退化的表現形式不只一種（壞 JSON、逾時、只回一張卡），攔窄了就會漏接。
        """
        try:
            output = await self.client.run_agent(agent_name, input_)
        except Exception as exc:  # noqa: BLE001 - 一律轉譯，讓對半重試接手
            raise StageProcessingError(f"抽取呼叫失敗：{type(exc).__name__}: {exc}") from exc
        if not isinstance(output, ExtractOutput):
            raise StageProcessingError(
                f"agent {agent_name!r} 回傳 {type(output).__name__}，預期 ExtractOutput"
            )
        return output

    # ── 切換點：兩條輸入路徑在此分流 ─────────────────────────────

    def _is_vision_row(self, row: CardRow) -> bool:
        """這一列該走影像路徑嗎？

        條件是「模式為 `vision_direct` 且該列有影像來源」。**不能只看
        `raw_text` 是否為空**——`two_stage` 下空的 `raw_text` 代表 OCR 失敗，
        那該讓它失敗，而不是靜靜改走另一條路徑產出品質未知的卡片。
        """
        mode = self.settings.ingest.mode if self.settings else "two_stage"
        return mode == "vision_direct" and bool(row.source) and row.ocr_source_page is not None

    async def _vision_input(self, row: CardRow) -> AgentInput:
        """影像路徑的輸入：影像 + 任務參數。"""
        image_b64 = await encode_image_b64(row.source)
        return build_image_input(image_b64, prompt=self._compose_header(row))

    async def _build_request(self, row: CardRow) -> tuple[str, AgentInput]:
        """決定「用哪個 agent、送什麼 input」。

        | 條件 | agent | input |
        |------|-------|-------|
        | 有 `raw_text` | `ExtractAgent` | 任務參數 + 教材本文 |
        | vision_direct 且有影像來源 | `VisionExtractAgent` | 影像 + 任務參數 |

        兩者共用同一個 `output_schema`，因此上層拿到的形狀完全相同。
        """
        if row.raw_text.strip():
            return TEXT_AGENT, self._compose_text_input(row)

        if self._is_vision_row(row):
            image_b64 = await encode_image_b64(row.source)
            return VISION_AGENT, build_image_input(image_b64, prompt=self._compose_header(row))

        raise StageProcessingError("此列既沒有 raw_text，也沒有可用的影像來源")

    def _compose_text_input(self, row: CardRow) -> str:
        """組出 prompt 期望的「任務參數 + 空行 + 教材本文」。

        prompt 是靜態的（`dynamic_prompt: false`），沒有模板變數可用，
        因此領域、牌組前綴等資訊只能隨輸入送進去。
        """
        header = self._compose_header(row)
        if not header:
            return row.raw_text
        return header + "\n\n" + row.raw_text

    def _compose_header(self, row: CardRow, chunk_label: str = "") -> str:
        """組出任務參數區塊，兩條路徑共用。

        prompt 是靜態的（`dynamic_prompt: false`），沒有模板變數可用，
        因此領域、牌組前綴等資訊只能隨輸入送進去。
        """
        params = {
            "領域": self.domain,
            "牌組前綴": self.deck_name,
            "卡片ID前綴": self._card_id_prefix_for(row, chunk_label),
            "來源": self.source,
            "釋義語言": self.card_language,
            # 分類名稱與釋義同語言。實測 E4B 對「參數」的服從度遠高於 prompt 中的
            # 規則——同一條要求寫在 prompt 裡整段被忽略（38/98 退回泛稱），
            # 提升為參數後才守得住
            "分類語言": self.card_language,
            "分類選項": self.deck_categories,
        }
        return "\n".join(f"{key}: {value}" for key, value in params.items() if value)

    def _card_id_prefix_for(self, row: CardRow, chunk_label: str = "") -> str | None:
        """把頁碼與段落標籤串進前綴，確保跨頁、跨段的序號不會互撞。

        每段是獨立一次呼叫，模型看不到別段，序號一律從 001 重新起算——
        沒有段落標籤就會整批撞號。
        """
        parts = [part for part in (self.card_id_prefix,) if part]
        if row.ocr_source_page is not None:
            parts.append(f"p{row.ocr_source_page}")
        if chunk_label:
            parts.append(chunk_label)
        return "_".join(parts) if parts else None

    # ── 套用結果 ─────────────────────────────────────────────────

    def _to_rows(self, cards_in: Sequence[ExtractedCard], source_row: CardRow) -> list[CardRow]:
        """把抽取結果轉成新列，並在此做 `card_id` 唯一性檢查。

        任一張卡的 id 有問題就讓整列失敗——半套結果比沒有結果更難收拾，
        修好前綴或 prompt 後重跑該列即可。
        """
        rows: list[CardRow] = []
        batch_ids: set[str] = set()

        for index, card in enumerate(cards_in, start=1):
            card_id = card.card_id.strip()
            if not card_id:
                raise StageProcessingError(f"第 {index} 張卡沒有 card_id")
            if card_id in self._seen_ids or card_id in batch_ids:
                raise StageProcessingError(
                    f"card_id duplicated: {card_id}"
                    "（若正在重跑同一頁，請先移除該頁先前產出的卡片列，"
                    "或改用不同的 card_id 前綴）"
                )
            batch_ids.add(card_id)
            rows.append(self._build_card_row(card, source_row))

        self._seen_ids |= batch_ids
        return rows

    def _build_card_row(self, card, source_row: CardRow) -> CardRow:  # noqa: ANN001
        row = CardRow(**card.to_card_fields())
        # 沿用來源列的追溯資訊
        row.ocr_source_page = source_row.ocr_source_page
        row.ocr_status = source_row.ocr_status
        # 這張卡的抽取已經完成，不該在下次執行時被再撈一次
        row.extract_status = StageStatus.DONE
        if not row.deck and self.deck_name:
            row.deck = self.deck_name
        if not row.source and self.source:
            row.source = self.source
        return row
