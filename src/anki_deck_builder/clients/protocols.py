"""外部服務的介面契約（服務層）。

依架構約束 1，所有外部服務（LLM、OCR、ComfyUI、TTS）都在此定義 Protocol，
`stages/` 只依賴這些抽象，不依賴具體實作。四個 Protocol 於 **Phase 1 一次定義完成**，
後續 phase 只實作、不新增。

ComfyUI 與 VOXCPM2 的實際規格尚未提供，簽章屬預留設計。
規格補齊後若與此處不符，**調整 Protocol 而非上層邏輯**（約束 1 的推論）。
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

#: agent 的輸入。純文字走 `str`；影像走 message list，
#: 影像以 `input_image` content item 加 base64 data URL 傳入（見 agent_factory README）。
#: `LimitAgentRunner.run(input_=...)` 本來就接受 `str | list`，兩條路徑共用同一個介面。
AgentInput = str | list[dict[str, Any]]


class LLMClientProtocol(Protocol):
    """LLM 呼叫。實作為 agent_factory 的薄適配層。"""

    async def run_agent(self, agent_name: str, input_: AgentInput) -> BaseModel:
        """以 `agents.yaml` 中宣告的 agent 執行一次呼叫。

        prompt、模型、structured output schema 全部由 `agents.yaml` 決定，
        呼叫端只給 agent 名稱與輸入。回傳值已由 agent_factory 依 `output_schema`
        解析為 Pydantic model，本專案不自行解析 JSON。

        `input_` 同時涵蓋兩種輸入模式：`two_stage` 送 `raw_text` 字串，
        `vision_direct` 送影像 message list（Phase 2）。

        Args:
            agent_name: `agents.yaml` 中的 `name` 欄位值。
            input_: 純文字或 message list。

        Returns:
            依該 agent 的 `output_schema` 解析後的 model。

        Raises:
            ExternalServiceError: 呼叫失敗，含 agent 名稱查無、逾時、速率限制等。
        """
        ...


class OCRClientProtocol(Protocol):
    """影像轉文字（Phase 2 實作）。

    與 LLM 走**同一套 agent_factory**，只是 `agents.yaml` 中指向 OCR 模型，
    不是另一個獨立的服務或 SDK。
    """

    async def recognize(self, image_b64: str) -> str:
        """辨識單張影像。

        影像以 base64 字串傳入而非檔案路徑，避免路徑編碼與跨平台問題；
        也讓 token 估算拿得到影像 header（遠端 URL 一律取不到尺寸，會被保守高估）。

        Args:
            image_b64: 影像的 base64 編碼內容（不含 `data:` 前綴）。

        Returns:
            辨識出的純文字，寫入該列的 `raw_text`。

        Raises:
            ExternalServiceError: 辨識失敗。
        """
        ...


class ImageGenClientProtocol(Protocol):
    """聯想圖生成（Phase 3 實作，ComfyUI HTTP API）。

    待確認：實際 workflow 尚未提供。節點 ID 與欄位名一律由 `config.py` 取得，
    實作中不得出現字面量節點 ID（約束 5）。
    """

    async def generate(
        self,
        positive_prompt: str,
        seed: int | None = None,
    ) -> bytes:
        """生成一張圖。

        負向 prompt、尺寸、batch size 屬設定，不在簽章中——它們對整批一致，
        由實作從 `config.py` 取得。

        Args:
            positive_prompt: 該列的 `image_prompt`（英文，且必須不含文字元素）。
            seed: 指定亂數種子；`None` 表示交由 workflow 決定。

        Returns:
            圖片的原始 bytes（PNG）。

        Raises:
            ExternalServiceError: 生成失敗或輪詢逾時。
        """
        ...


class TTSClientProtocol(Protocol):
    """語音生成（Phase 4 實作，VOXCPM2）。

    待確認：VOXCPM2 的介面規格尚未提供，`speaker_id` 的實際型別
    （字串 id／整數索引／模型檔路徑）也待確認，目前以字串承接。
    """

    async def synthesize(
        self,
        text: str,
        speaker_id: str | None = None,
        language: str = "ja",
        speed: float = 1.0,
    ) -> bytes:
        """合成一段語音。

        單字與例句各呼叫一次，分別對應 `audio_front` 與 `audio_back`，
        兩者狀態獨立，任一失敗不影響另一個。

        Args:
            text: 要唸的文字，取自 `tts_front_text` 或 `tts_back_text`。
            speaker_id: 語者；`None` 表示採用設定的預設值。
            language: 語言代碼。
            speed: 語速倍率。

        Returns:
            音訊的原始 bytes。

        Raises:
            ExternalServiceError: 合成失敗或逾時。
        """
        ...
