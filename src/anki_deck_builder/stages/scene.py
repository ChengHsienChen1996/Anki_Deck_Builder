"""階段 ③：聯想圖場景（流程層）。

讀每張卡的 `front`／`back`／`example`，請 `SceneAgent` 想一個**模型無關**的視覺場景，
寫進 `image_scene`。這一欄是語義層，換文生圖模型時**不重生**——重生的是下一階段
由它產出的 `image_prompt`（語法層）。

## 為什麼從 extract 拆出來

E4B（8B）在單次呼叫裡要同時做 17 欄結構化填表與一次跨語言跨模態的創意視覺轉譯，
兩件事型態完全不同。2026-09-01 掃描實際資料：把〈避開會帶出文字的道具〉的規則寫進
`extract_cards.md` 之後，違規率沒有下降（規則前 67/308，規則後 4/13）——
規則進去了，模型沒照做。理由與 `EnrichAgent` 當初被拆出去的完全相同。

## 產出檢查為什麼寬嚴不一

`clean()` 容忍引號、code fence 與標籤前綴，卻讓多行內容進入檢查後被擋下。
這不是隨意——前者是這個本地模型**已知的無害習慣**
（`scripts/rewrite-image-prompts.py` 的 `clean()` 早就在處理同一批徵狀），
去掉包裝後語義一字不差；後者代表模型寫了說明文字，那時第一行是不是答案並無保證。
無害的包裝就拆掉，有疑慮的形狀就重試。
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
PROGRESS_LABEL = "構思聯想圖場景"

#: `agents.yaml` 中的 agent 名稱
SCENE_AGENT = "SceneAgent"

#: 嘗試次數。與影像抽取同理——退化多半是隨機的，重試一次就能拿到正常結果
MAX_ATTEMPTS = 2

#: 場景長度的上下限（字元）。太短畫不出東西，太長讓圖像模型顧此失彼。
#: 這兩個數字是護欄不是品味：它們擋的是「只回一個詞」與「把整段說明吐進來」
MIN_SCENE_CHARS = 15
MAX_SCENE_CHARS = 400

#: 屬於**語法層**的詞，出現在語義層就是層次被打破了。
#:
#: 這份清單刻意只收「任何 profile 都不會需要語義層提供」的風格／品質詞——
#: 不收 `no text` 這類否定敘述，因為 `a blank sign with no text` 是合法的場景描述，
#: 收進來會誤殺。觸發詞（`Anime.` 等）也不收：那是 profile 專屬的，
#: 寫死在這裡等於把模型知識搬回程式（約束 5）
STYLE_LAYER_MARKERS: tuple[str, ...] = (
    "cinematic lighting",
    "muted color palette",
    "soft shadows",
    "no watermark",
    "masterpiece",
    "best quality",
    "absurdres",
)

#: 模型偶爾會在答案前加一行標籤（`image_scene:`、`Scene:`）。以「短且以冒號結尾」辨識
_LABEL_LINE = re.compile(r"^.{0,24}[:：]\s*$")


def clean(reply: str) -> str:
    """拆掉模型慣性加上的包裝，回傳它真正想說的那一行。

    只拆包裝、不修內容——修內容會讓壞掉的產出看起來像好的，
    而這個階段的價值正是讓壞產出在這裡就被擋下。
    """
    text = reply.strip().strip("`").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # 丟掉開頭的標籤行；`Scene:` 這種本身不是答案
    while lines and _LABEL_LINE.match(lines[0]):
        lines.pop(0)
    if not lines:
        return ""
    if len(lines) > 1:
        # 多行代表模型還寫了別的東西。不猜哪一行是答案，原樣交給檢查擋下
        return "\n".join(lines)
    return lines[0].strip("\"'").strip().rstrip(".")


def check_scene(scene: str) -> None:
    """產出不合格時拋 `StageProcessingError`，交給重試。

    Raises:
        StageProcessingError: 空白、多行、長度越界，或混進了語法層的詞。
    """
    if not scene:
        raise StageProcessingError("場景為空")
    if "\n" in scene:
        first = scene.splitlines()[0][:40]
        raise StageProcessingError(f"場景不是單獨一行，模型多寫了說明（首行：{first}…）")
    if len(scene) < MIN_SCENE_CHARS:
        raise StageProcessingError(f"場景只有 {len(scene)} 個字元，太短：{scene!r}")
    if len(scene) > MAX_SCENE_CHARS:
        raise StageProcessingError(f"場景長達 {len(scene)} 個字元，超過上限 {MAX_SCENE_CHARS}")

    lowered = scene.lower()
    leaked = [marker for marker in STYLE_LAYER_MARKERS if marker in lowered]
    if leaked:
        raise StageProcessingError(
            f"場景含語法層的風格詞 {leaked}——那些由 prompt 階段依 profile 加，"
            "寫進語義層會在換模型時被重複帶進去"
        )


def build_input(row: CardRow) -> str:
    """給模型的輸入：這張卡的語義。

    只送語義相關的四欄。`deck`、`tags`、`difficulty` 這些與畫面無關，
    送進去只會佔 context 並讓模型分心。
    """
    parts = [f"條目：{row.front}", f"釋義：{row.back}"]
    if row.example.strip():
        # example 以字面 \n 分隔原文與譯文（見 extract_cards.md），還原成換行才好讀
        parts.append("例句：" + row.example.replace("\\n", "\n"))
    if row.hint.strip():
        parts.append(f"性質：{row.hint}")
    return "\n".join(parts)


@register_stage("scene")
class SceneStage(BaseStage):
    """把卡片語義變成模型無關的 `image_scene`。"""

    def __init__(
        self,
        client: LLMClientProtocol,
        settings: Settings | None = None,
        progress_stream: TextIO | None = None,
    ) -> None:
        """
        Args:
            client: LLM client（依 Protocol 注入，測試以假實作替換）。
            settings: 目前僅供未來擴充；本階段不從中取值。
            progress_stream: 進度條輸出位置，`None` 為預設（同 `image` 階段）。
        """
        self.client = client
        self.settings = settings
        # 併發固定 1。理由同 extract：本地模型在單張 GPU 上序列化執行，
        # 併發不會更快，而排隊時逾時計時器照樣在跑
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
        """加上進度條後交給骨架執行（作法同 `image` 階段）。"""
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
            return await self._scene_for(row)
        finally:
            if self._progress is not None:
                self._progress.advance()

    async def _scene_for(self, row: CardRow) -> Sequence[CardRow]:
        if not row.card_id:
            # ocr／extract 留下的來源列，不是卡片，不該有場景。
            # 同 `image.py` 的防護——那一列的 scene_status 從未被動過因而是 pending，
            # 會被選進本階段
            return ()

        if not row.front.strip():
            raise StageProcessingError(
                f"{row.card_id} 沒有 front，無法構思場景（該欄由階段 ② extract 填寫）"
            )

        row.image_scene = await self._generate(build_input(row))
        return ()

    async def _generate(self, input_: AgentInput) -> str:
        """呼叫一次並檢查產出；不合格重試一次。"""
        last: StageProcessingError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                scene = clean(await self._call(input_))
                check_scene(scene)
                return scene
            except StageProcessingError as exc:
                last = exc
                if attempt == MAX_ATTEMPTS:
                    raise
        raise last  # pragma: no cover - 迴圈必定 return 或 raise

    async def _call(self, input_: AgentInput) -> str:
        """送出一次呼叫並確認型別。

        任何失敗都轉成 `StageProcessingError`，讓重試接得住——退化的表現形式
        不只一種（壞內容、逾時、空回應），攔窄了就會漏接（同 `extract.py`）。
        """
        try:
            reply = await self.client.run_agent(SCENE_AGENT, input_)
        except Exception as exc:  # noqa: BLE001 - 一律轉譯，讓重試接手
            raise StageProcessingError(
                f"場景呼叫失敗：{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(reply, str):
            raise StageProcessingError(
                f"agent {SCENE_AGENT!r} 應回傳純文字，卻收到 {type(reply).__name__}"
                "（請確認 agents.yaml 未替它宣告 output_schema）"
            )
        return reply
