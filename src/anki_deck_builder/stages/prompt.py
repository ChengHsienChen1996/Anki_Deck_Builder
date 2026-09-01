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

#: **這裡刻意沒有「語義有沒有偏離原場景」的檢查。**
#:
#: 做過，移掉了。用場景與產出的內容詞重疊率當指標，2026-09-01 在約 330 次真實改寫上
#: 累計 **5 次誤殺、0 次真陽性**，門檻從 0.5 調到 0.3 再調到 0.15 都擋不住。
#: 三個缺陷是結構性的，不是門檻沒調好：
#:
#: 1. **同義替換**——`gesturing` 寫成 `hand outstretched as if presenting`。
#:    散文改寫本來就會這樣，而詞袋比對只看字面。
#: 2. **短詞被排除**——`ant` 只有三個字母，不算內容詞；一張畫螞蟻的卡重疊率是 0%。
#: 3. **不管詞形變化**——`tasting` 與 `tastes` 在字面比對下是兩個詞。
#:
#: 要真的判斷語義偏離需要語意向量，那是另一個模型呼叫與一份相依，
#: 為了一個沒抓到過真陽性的檢查不值得。改寫偏離題材由 A/B 與人工抽驗把關
#: （見 logs/2026-09-01_eval_prompt-layer-ab.md）。
#:
#: 下面留的都是**結構檢查**：形狀不對一眼可判，且不會誤傷正確產出。

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


def check_prompt(prompt: str) -> None:
    """產出的**形狀**不合格時拋 `StageProcessingError`，交給重試。

    只檢查形狀，不檢查語義——理由見上方常數區的說明。

    Raises:
        StageProcessingError: 空白、多行，或長度越界。
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
                check_prompt(prompt)
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
