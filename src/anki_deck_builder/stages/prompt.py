"""階段 ④：聯想圖 prompt（流程層）。

讀每張卡的 `image_scene`（模型無關的語義層），依當前 **profile** 改寫成該文生圖
模型要的語法，寫進 `image_prompt`。`image` 階段照舊只讀 `image_prompt`。

## 一個 profile ＝ 一份 prompt 檔 ＋ 一份 workflow

模型專屬的三件事——目標語法（自然語言敘述 vs 逗號 tag）、LoRA 觸發詞、風格後綴——
全部寫在 `IMAGE_PROMPT_AGENT` 指到的那份 prompt 檔裡，**不散在 `.env`**。
Phase 7 曾把觸發詞做成 `COMFYUI_PROMPT_PREFIX`，Phase 8 移除了它：同一件事有兩個
機制，換模型時忘了清哪一邊就會產生無法預期的交互作用。

副作用是可除錯性的淨收益：**CSV 裡的 `image_prompt` 就是送進 ComfyUI 的完整字串**，
`comfyui_client` 不再做任何加工。

## 為什麼與 `scene` 是兩個階段而不是一個內部兩步

要能獨立重跑。換模型時只重生語法層，人工編修過的語義層一個字都不動——
合成一個階段的話 `--force` 會連語義層一起洗掉，等於白拆。
理由同 `audio_front`／`audio_back` 當初拆開。

## 觸發詞為什麼不做程式檢查

要檢查就得把觸發詞再變成一個系統參數，正是本階段要消除的東西（使用者裁示，
2026-09-01）。改以三道非參數的把關：profile 的 prompt 檔把它寫成硬性首句、
A/B 逐張確認、全量產出後 `grep` 抽樣驗收。
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import TextIO

from ..clients.protocols import AgentInput, LLMClientProtocol
from ..config import Settings
from ..exceptions import StageProcessingError
from ..schemas import CardRow
from ..state import select_pending
from .base import BaseStage, register_stage
from .progress import ProgressReporter

#: 進度條上顯示的階段名稱
PROGRESS_LABEL = "改寫聯想圖 prompt"

#: 沒有帶 settings 時（測試路徑）用的 agent
DEFAULT_AGENT = "ImagePromptFluxAgent"

#: 嘗試次數。理由同 scene：退化多半是隨機的，重試一次就能拿到正常結果
MAX_ATTEMPTS = 2

#: 產出長度的上下限（字元）。上限比 scene 寬——自然語言敘述本來就比 tag 串長，
#: 而 SDXL profile 還要接上 100 字元出頭的後綴
MIN_PROMPT_CHARS = 20
MAX_PROMPT_CHARS = 900

#: 語義保底的門檻。**這是災難偵測器，不是品質閘。**
#:
#: 它只該在一種情形觸發：模型完全無視場景、自己想了一張圖。那種情形重疊率是 0。
#: 訂得比那高一點都會誤殺，因為**詞袋重疊率分不出「同義改寫」與「換題材」**，
#: 而同義替換正是散文改寫在做的事。2026-09-01 實測兩次誤殺：
#:
#: 1. 場景 `a small pile of coins next to a much larger overflowing pile`
#:    → `...rests beside an enormous, overflowing mound of currency`（重疊 44%）
#:    ——`much larger` 換成 `enormous`、`next to` 換成 `beside`
#: 2. 場景 `a person gesturing towards a large group of diverse objects`
#:    → `A figure stands amidst an overwhelming collection of diverse objects,
#:    their hand outstretched as if presenting`（重疊 20%）
#:    ——`gesturing` 寫成 `hand outstretched as if presenting`，語義完整保留，
#:    但幾乎每個實詞都換了同義詞
#:
#: 門檻從 0.5 調到 0.3 只是治症狀，第二例照樣被擋。
#:
#: 觀察到的 16 次真實改寫裡，這個檢查貢獻 **2 次誤殺、0 次真陽性**。
#: 因此壓到 0.15：只擋「重疊近乎為零」的災難，其餘交給 Task 8.5 的 A/B 與人工抽驗。
#: 誤殺會讓好產出變成 failed 列、要使用者手動重跑，代價比漏放高。
MIN_SEMANTIC_OVERLAP = 0.15

#: 算重疊率時只看夠長的英文詞——冠詞、介系詞在任何句子裡都有，算進去會稀釋訊號
_CONTENT_WORD = re.compile(r"[a-z]{4,}")

#: 模型偶爾會在答案前加一行標籤（`image_prompt:`、`Prompt:`）。同 `scene.py`
_LABEL_LINE = re.compile(r"^.{0,24}[:：]\s*$")


def clean(reply: str) -> str:
    """拆掉模型慣性加上的包裝，回傳它真正想說的那一行。

    與 `scene.clean()` 的取捨相同：只拆包裝、不修內容。**尤其不補觸發詞**——
    補了就等於把觸發詞搬回程式，那是本階段刻意不做的事，而且會讓
    「模型漏加觸發詞」這個問題被永久藏起來。
    """
    text = reply.strip().strip("`").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    while lines and _LABEL_LINE.match(lines[0]):
        lines.pop(0)
    if not lines:
        return ""
    if len(lines) > 1:
        # 自然語言 profile 的產出可能本來就有多句，但**不該有多行**。
        # 多行代表模型另外寫了說明，交給檢查擋下
        return "\n".join(lines)
    return lines[0].strip("\"'").strip()


def content_words(text: str) -> set[str]:
    """取出用來算語義重疊的內容詞。"""
    return set(_CONTENT_WORD.findall(text.lower()))


def semantic_overlap(scene: str, prompt: str) -> float:
    """場景的內容詞有多少比例出現在產出裡。場景沒有內容詞時回 1.0（無從判斷就不擋）。"""
    wanted = content_words(scene)
    if not wanted:
        return 1.0
    return len(wanted & content_words(prompt)) / len(wanted)


def check_prompt(scene: str, prompt: str) -> None:
    """產出不合格時拋 `StageProcessingError`，交給重試。

    Raises:
        StageProcessingError: 空白、多行、長度越界，或語義偏離原場景。
    """
    if not prompt:
        raise StageProcessingError("prompt 為空")
    if "\n" in prompt:
        first = prompt.splitlines()[0][:40]
        raise StageProcessingError(f"prompt 不是單獨一行，模型多寫了說明（首行：{first}…）")
    if len(prompt) < MIN_PROMPT_CHARS:
        raise StageProcessingError(f"prompt 只有 {len(prompt)} 個字元，太短：{prompt!r}")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise StageProcessingError(
            f"prompt 長達 {len(prompt)} 個字元，超過上限 {MAX_PROMPT_CHARS}"
        )

    overlap = semantic_overlap(scene, prompt)
    if overlap < MIN_SEMANTIC_OVERLAP:
        # 訊息帶上產出本身：少了它，要判斷是誤殺還是真偏題就得再跑一次
        # （2026-09-01 調這個門檻時就是這樣多花了一輪）
        raise StageProcessingError(
            f"產出與原場景的內容詞只重疊 {overlap:.0%}（門檻 {MIN_SEMANTIC_OVERLAP:.0%}）"
            f"——改寫應該換語法而不是換題材。產出：{prompt[:120]!r}"
        )


@register_stage("prompt")
class PromptStage(BaseStage):
    """把 `image_scene` 依 profile 改寫成 `image_prompt`。"""

    def __init__(
        self,
        client: LLMClientProtocol,
        settings: Settings | None = None,
        agent: str | None = None,
        progress_stream: TextIO | None = None,
    ) -> None:
        """
        Args:
            client: LLM client（依 Protocol 注入，測試以假實作替換）。
            settings: profile 的來源（`IMAGE_PROMPT_AGENT`）。
            agent: 直接指定 agent，優先於 settings。測試與一次性比較用。
            progress_stream: 進度條輸出位置。
        """
        self.client = client
        self.settings = settings
        self.agent = agent or (
            settings.image_prompt.agent if settings is not None else DEFAULT_AGENT
        )
        # 併發固定 1，理由同 scene 與 extract
        self.concurrency = 1
        self._progress_stream = progress_stream
        self._progress: ProgressReporter | None = None

    async def run(
        self,
        store,  # noqa: ANN001 - 型別同 BaseStage.run
        force: bool = False,
        only_failed: bool = False,
        card_ids: Collection[str] | None = None,
    ):
        """加上進度條後交給骨架執行（作法同 `scene`／`image` 階段）。"""
        rows = await store.read() if store.exists() else []
        targets = select_pending(
            rows, self.name, force=force, only_failed=only_failed, card_ids=card_ids
        )
        self._progress = ProgressReporter(
            PROGRESS_LABEL, len(targets), stream=self._progress_stream
        )
        self._progress.start()
        try:
            return await super().run(
                store, force=force, only_failed=only_failed, card_ids=card_ids
            )
        finally:
            self._progress.finish()

    async def process_row(self, row: CardRow) -> Sequence[CardRow]:
        try:
            return await self._prompt_for(row)
        finally:
            if self._progress is not None:
                self._progress.advance()

    async def _prompt_for(self, row: CardRow) -> Sequence[CardRow]:
        if not row.card_id:
            # ocr／extract 留下的來源列，不是卡片。同 `scene.py`、`image.py` 的防護
            return ()

        scene = row.image_scene.strip()
        if not scene:
            raise StageProcessingError(
                f"{row.card_id} 沒有 image_scene，無法改寫"
                "（該欄由階段 ③ scene 填寫）"
            )

        row.image_prompt = await self._generate(scene)
        return ()

    async def _generate(self, scene: str) -> str:
        """呼叫一次並檢查產出；不合格重試一次。"""
        last: StageProcessingError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                prompt = clean(await self._call(scene))
                check_prompt(scene, prompt)
                return prompt
            except StageProcessingError as exc:
                last = exc
                if attempt == MAX_ATTEMPTS:
                    raise
        raise last  # pragma: no cover - 迴圈必定 return 或 raise

    async def _call(self, input_: AgentInput) -> str:
        """送出一次呼叫並確認型別。任何失敗一律轉譯，讓重試接得住（同 `scene.py`）。"""
        try:
            reply = await self.client.run_agent(self.agent, input_)
        except Exception as exc:  # noqa: BLE001 - 一律轉譯，讓重試接手
            raise StageProcessingError(
                f"prompt 改寫呼叫失敗：{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(reply, str):
            raise StageProcessingError(
                f"agent {self.agent!r} 應回傳純文字，卻收到 {type(reply).__name__}"
                "（請確認 agents.yaml 未替它宣告 output_schema）"
            )
        return reply
