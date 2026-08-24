"""階段 ④：語音生成（流程層）。

每張卡有兩段獨立的語音：`audio_front` 唸單字本身、`audio_back` 唸例句。
兩者狀態獨立，任一失敗不影響另一個，可各自重跑。

## 為什麼是兩個階段，而不是一個階段處理兩件事

`state/selector.py` 的 `STAGE_FIELDS` 從 Phase 1 起就把 audio 拆成
**`audio_front` 與 `audio_back` 兩個階段名**——沒有 `"audio"` 這個鍵，
`@register_stage("audio")` 會直接拋 `KeyError`。

順著這個設計走，兩側各註冊一個薄子類，共用邏輯放在 `_AudioStage`：

- `BaseStage` 一個字都不用改（它的「一列一狀態」前提在這裡照樣成立）
- `--side front` 的隔離性由「**根本沒有執行另一個階段**」保證，
  而不是靠某段邏輯記得繞開 `audio_back_status`
- `--only-failed`、`--force`、進度顯示全部沿用既有機制

`--side` 到階段類別的對應表是 `SIDE_STAGES`，由 `cli.py` 查表 dispatch。

## `tts_front_text` 為空時的退路

實測 308 張卡有 116 張的 `tts_front_text` 是空的，**全部都是 `reading` 也為空的
非日語詞條**。這不是資料缺漏——`prompts/extract_cards.md` 對該欄位本來就規定
「有 `reading` 時填讀音，**沒有讀音概念的領域填 `front` 本身**」，只是 8B 模型
沒穩定照做（同 Phase 2 日誌記的「參數比 prompt 規則有效得多」）。

因此 front 側依序退回 `reading`、`front`：那正是 prompt 規範本來就要的結果，
只是改由程式保證。**back 側沒有對應的退路**——`back` 是釋義而非例句，
拿來唸會變成另一件事；例句本來就可以不存在（規範明寫「例句是空的話，留空」）。

## 落腳處與 seed 的取捨同 image 階段

媒體根目錄取「中間 CSV 所在目錄」（與 `pack` 的 `media_root` 一致），
相對路徑寫回 CSV。與 image 不同的是**語音沒有 seed**——`voxcpm` 每次生成
都會有細微差異，重跑不保證位元相同。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, TextIO

from ..clients.protocols import TTSClientProtocol
from ..config import Settings
from ..exceptions import StageProcessingError
from ..schemas import CardRow
from ..state import select_pending
from .base import BaseStage, register_stage
from .progress import ProgressReporter

#: 音檔在工作目錄與 ZIP 內的共同相對位置（見 `stages/pack.py` 的 `MEDIA_DIRS`）
AUDIO_SUBDIR = "media/audio"


class _AudioStage(BaseStage):
    """兩側共用的合成邏輯。**不註冊**，由下方兩個子類指定要處理哪一側。"""

    #: `--side` 的值，同時是檔名後綴
    side: ClassVar[str] = ""
    #: 文字來源欄位。明寫而不以 f-string 組出——`selector.py` 對欄位名的要求
    text_field: ClassVar[str] = ""
    #: `text_field` 為空時依序改用的欄位（見模組 docstring）
    fallback_fields: ClassVar[tuple[str, ...]] = ()
    #: 回填路徑的欄位
    media_field: ClassVar[str] = ""
    #: 進度條上顯示的名稱
    progress_label: ClassVar[str] = ""

    def __init__(
        self,
        client: TTSClientProtocol,
        settings: Settings | None = None,
        media_root: str | Path | None = None,
        progress_stream: TextIO | None = None,
    ) -> None:
        """
        Args:
            client: TTS client（依 Protocol 注入，測試以假實作替換）。
            settings: 併發上限的來源。`None` 時併發為 1。
            media_root: 媒體檔的根目錄。預設於 `run()` 取中間 CSV 所在目錄，
                與 `pack` 的 `media_root` 一致。
            progress_stream: 進度輸出目的地，預設 `sys.stderr`。
        """
        self.client = client
        self.settings = settings
        self._media_root = Path(media_root) if media_root is not None else None
        self._progress_stream = progress_stream
        self._progress: ProgressReporter | None = None
        # 本行程內的 GPU 推論本就序列化，設 1 以外的值不會更快
        self.concurrency = settings.tts.concurrency if settings else 1
        # 單段數秒，每列寫回一次的成本可忽略；換取中斷後不必重合成已完成的段落
        self.checkpoint_every = 1

    def validate_settings(self) -> None:
        """階段開始前確認模型路徑與音色檔案。

        模型載入要 77 秒，設定錯誤不該讓使用者等完才知道。

        Raises:
            ConfigurationError: 模型路徑或音色檔案有問題。
        """
        self.client.validate()

    async def run(
        self,
        store,  # noqa: ANN001 - 型別同 BaseStage.run
        force: bool = False,
        only_failed: bool = False,
    ):
        """記下媒體根目錄、備好進度條，再交給骨架執行。

        兩者的理由同 `image.py`：`process_row()` 拿不到 store，而落腳處必須與
        `pack` 的 `media_root` 一致；進度條的總數骨架也不會往外傳。
        """
        if self._media_root is None:
            self._media_root = Path(store.path).parent

        rows = await store.read() if store.exists() else []
        targets = select_pending(rows, self.name, force=force, only_failed=only_failed)
        self._progress = ProgressReporter(
            self.progress_label, len(targets), stream=self._progress_stream
        )
        self._progress.start()
        try:
            return await super().run(store, force=force, only_failed=only_failed)
        finally:
            self._progress.finish()

    async def process_row(self, row: CardRow) -> Sequence[CardRow]:
        """合成這一列本側的語音並回填路徑。

        失敗時直接拋例外，由骨架寫入本側的 `*_error`、標 `failed` 並繼續下一列——
        **另一側完全不受影響**，它是另一個階段的事。
        """
        try:
            return await self._synthesize_for(row)
        finally:
            if self._progress is not None:
                self._progress.advance()

    async def _synthesize_for(self, row: CardRow) -> Sequence[CardRow]:
        if not row.card_id:
            # ocr／extract 留下的來源列，不是卡片。它的 audio_*_status 從未被動過
            # 因而是 pending，會被選進本階段（同 `image.py` 的防護）
            return ()

        text = self._text_for(row)
        if not text:
            sources = "、".join(self.source_fields())
            raise StageProcessingError(
                f"{row.card_id} 沒有可唸的文字（{sources} 全為空），無法生成語音"
                "（這些欄位由階段 ② extract 填寫）"
            )

        wav = await self.client.synthesize(text)
        relative = f"{AUDIO_SUBDIR}/{row.card_id}_{self.side}.wav"
        await self._write(self._resolve_media_root() / relative, wav)
        setattr(row, self.media_field, relative)
        return ()

    def source_fields(self) -> tuple[str, ...]:
        """這一側依序考慮哪些欄位。子類可依設定覆寫。"""
        return (self.text_field, *self.fallback_fields)

    def _text_for(self, row: CardRow) -> str:
        """取出這一側要唸的文字，必要時走退路。"""
        for field in self.source_fields():
            text = getattr(row, field).strip()
            if text:
                return text
        return ""

    def _resolve_media_root(self) -> Path:
        if self._media_root is not None:
            return self._media_root
        if self.settings is not None:
            return self.settings.paths.work_dir
        return Path("./work")

    @staticmethod
    async def _write(path: Path, content: bytes) -> None:
        """寫檔。建立目錄與寫入都是同步 I/O，包進執行緒避免卡住 event loop。"""

        def write_sync() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        await asyncio.to_thread(write_sync)


@register_stage("audio_front")
class AudioFrontStage(_AudioStage):
    """卡片正面：唸單字本身。"""

    side = "front"
    text_field = "tts_front_text"
    #: 沒有讀音就唸詞條本身——`prompts/extract_cards.md` 對 tts_front_text 的規範
    fallback_fields = ("reading", "front")
    media_field = "audio_front"
    progress_label = "生成語音（單字）"


@register_stage("audio_back")
class AudioBackStage(_AudioStage):
    """卡片背面：唸例句。"""

    side = "back"
    text_field = "tts_back_text"
    #: 沒有退路：`back` 是釋義不是例句，拿來唸會變成另一件事
    fallback_fields = ()
    media_field = "audio_back"
    progress_label = "生成語音（例句）"

    def source_fields(self) -> tuple[str, ...]:
        """要不要連譯文一起唸，是「讀哪個欄位」的選擇。

        `example` 存的是「原文＋譯文」（顯示用），`tts_back_text` 存的是
        「只有原文」（要唸的）——兩者從 Phase 1 就是分開的欄位。因此
        `VOXCPM2_SPEAK_TRANSLATION` 只需切換讀哪一個，**不必去猜哪一段是譯文**。
        用字串切割做這件事在日文牌組會整句刪光（日文例句本身就含漢字）。
        """
        if self.settings is not None and self.settings.tts.speak_translation:
            return ("example", self.text_field)
        return (self.text_field,)


#: `--side` 的值 → 要執行的階段。`both` 由呼叫端展開為兩者，順序即此處的順序。
SIDE_STAGES: dict[str, type[_AudioStage]] = {
    "front": AudioFrontStage,
    "back": AudioBackStage,
}


def stages_for_side(side: str) -> list[type[_AudioStage]]:
    """把 `--side` 的值展開成要依序執行的階段類別。

    Raises:
        KeyError: 不合法的 side 值。
    """
    if side == "both":
        return list(SIDE_STAGES.values())
    try:
        return [SIDE_STAGES[side]]
    except KeyError:
        raise KeyError(
            f"未知的 side {side!r}，合法值：{', '.join(SIDE_STAGES)}, both"
        ) from None
