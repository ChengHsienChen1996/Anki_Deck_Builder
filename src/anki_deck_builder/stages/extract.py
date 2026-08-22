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

from collections.abc import Sequence

from ..clients.image_input import build_image_input, encode_image_b64
from ..clients.protocols import AgentInput, LLMClientProtocol
from ..config import Settings
from ..exceptions import StageProcessingError
from ..schemas import CardRow, ExtractOutput, StageStatus
from .base import BaseStage, register_stage

#: 文字路徑使用的 agent（`agents.yaml` 中的 name）
TEXT_AGENT = "ExtractAgent"
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
    ) -> None:
        """
        Args:
            client: LLM client（依 Protocol 注入，測試以假實作替換）。
            settings: 併發上限與輸入模式的來源。`None` 時併發為 1、走文字路徑。
            deck_name: 牌組名稱前綴（CLI 的 `--deck-name`）。未指定時由 LLM 依內容判定。
            domain: 學習領域。未指定時由 LLM 依內容判定。
            source: 寫入 `source` 欄位的值，例如「單字書 p.333」。
            card_id_prefix: `card_id` 前綴。實際送出的前綴會再串上頁碼，
                因為每列是獨立一次呼叫，模型看不到別頁，不加頁碼會跨頁撞號。
        """
        self.client = client
        self.settings = settings
        self.deck_name = deck_name
        self.domain = domain
        self.source = source
        self.card_id_prefix = card_id_prefix
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

        agent_name, input_ = await self._build_request(row)
        output = await self.client.run_agent(agent_name, input_)
        if not isinstance(output, ExtractOutput):
            raise StageProcessingError(
                f"agent {agent_name!r} 回傳 {type(output).__name__}，預期 ExtractOutput"
            )

        return self._to_rows(output, row)

    # ── 切換點：兩條輸入路徑在此分流 ─────────────────────────────

    def _is_vision_row(self, row: CardRow) -> bool:
        """這一列該走影像路徑嗎？

        條件是「模式為 `vision_direct` 且該列有影像來源」。**不能只看
        `raw_text` 是否為空**——`two_stage` 下空的 `raw_text` 代表 OCR 失敗，
        那該讓它失敗，而不是靜靜改走另一條路徑產出品質未知的卡片。
        """
        mode = self.settings.ingest.mode if self.settings else "two_stage"
        return mode == "vision_direct" and bool(row.source) and row.ocr_source_page is not None

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

    def _compose_header(self, row: CardRow) -> str:
        """組出任務參數區塊，兩條路徑共用。

        prompt 是靜態的（`dynamic_prompt: false`），沒有模板變數可用，
        因此領域、牌組前綴等資訊只能隨輸入送進去。
        """
        params = {
            "領域": self.domain,
            "牌組前綴": self.deck_name,
            "卡片ID前綴": self._card_id_prefix_for(row),
            "來源": self.source,
        }
        return "\n".join(f"{key}: {value}" for key, value in params.items() if value)

    def _card_id_prefix_for(self, row: CardRow) -> str | None:
        """把頁碼串進前綴，確保跨頁的序號不會互撞。"""
        parts = [part for part in (self.card_id_prefix,) if part]
        if row.ocr_source_page is not None:
            parts.append(f"p{row.ocr_source_page}")
        return "_".join(parts) if parts else None

    # ── 套用結果 ─────────────────────────────────────────────────

    def _to_rows(self, output: ExtractOutput, source_row: CardRow) -> list[CardRow]:
        """把抽取結果轉成新列，並在此做 `card_id` 唯一性檢查。

        任一張卡的 id 有問題就讓整列失敗——半套結果比沒有結果更難收拾，
        修好前綴或 prompt 後重跑該列即可。
        """
        rows: list[CardRow] = []
        batch_ids: set[str] = set()

        for index, card in enumerate(output.cards, start=1):
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
