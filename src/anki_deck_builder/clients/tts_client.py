"""VOXCPM2 語音生成（服務層）。

實作 `TTSClientProtocol`。`voxcpm` 是**本機 Python 套件**，推論跑在本行程內的 GPU 上，
不是 HTTP 服務——所以沒有 endpoint、沒有連線逾時，也沒有 `language`／`speed`／
`speaker_id` 這類參數（語言由文字本身決定，音色由參考音檔決定）。

## 模型只建構一次

實測建構耗時 **77 秒**（含套件自己的暖機），權重佔 VRAM 約 6.3 GB。每次呼叫重建
等於災難——一副 308 張卡要合成 616 段，光載入就是七小時。實例快取在 `_model`，
首次載入以 `asyncio.Lock` 保護。

## 生成是同步阻塞的 GPU 呼叫

`VoxCPM.generate()` 會佔住 CPU 執行緒數秒，直接跑在事件迴圈上會讓進度條與其他
協程一起凍住。一律以 `asyncio.to_thread` 包起來。

## 不自行重試

套件內建 `retry_badcase`（預設開、最多 3 次、以音長／文字長度比判斷）。
再包一層只會讓失敗的案例多花三倍時間。

## 實測數據（2026-08-24，RTX 3090）

| 項目 | 值 |
|------|-----|
| 建構耗時 | 77.4s（`optimize=True`，含套件暖機） |
| 取樣率 | **48000 Hz** |
| 單字（3 字） | 0.3～2.0s |
| 例句（10～49 字） | 1.1～3.6s |
| VRAM | 載入後 +6.3 GB，推論期峰值再 +1 GB |
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import soundfile as sf

from ..config import TTSSettings
from ..exceptions import ConfigurationError, ExternalServiceError

logger = logging.getLogger(__name__)

#: 寫檔的取樣格式。模型輸出 float32，直接寫成 WAV 會是 IEEE float——檔案大一倍，
#: 且不是每個瀏覽器都播得動。記憶引擎在瀏覽器裡播放，PCM_16 是最保險的選擇
WAV_SUBTYPE = "PCM_16"


class VoxCPMClient:
    """VOXCPM2 的適配層。

    音色與生成參數全部自 `TTSSettings` 取得，不進 `synthesize()` 的簽章——
    它們對整套牌組一致（約束 5）。
    """

    def __init__(
        self,
        settings: TTSSettings,
        model_factory: Callable[[], Any] | None = None,
    ) -> None:
        """
        Args:
            settings: `VOXCPM2_*` 系列設定。
            model_factory: 建構模型的函式。預設為延後匯入 `voxcpm` 後建構——
                測試注入假工廠即可完全不碰那 4.58 GB 的權重，也不必付出
                匯入 torch 的代價（見 docs/llm-integration.md 對本地模型的要求）。
        """
        self._settings = settings
        self._model_factory = model_factory or self._build_model
        self._model: Any | None = None
        self._lock = asyncio.Lock()
        self._warned_random_voice = False

    # ── 設定 ─────────────────────────────────────────────────────

    def validate(self) -> None:
        """階段開始前的設定檢查，由 `AudioStage.validate_settings()` 呼叫。

        在**載入模型之前**做——載入要 77 秒，設定錯誤不該讓使用者等完才知道。

        Raises:
            ConfigurationError: 模型路徑未設定或不存在，或音色檔案指向不存在的路徑。
        """
        model_path = self._settings.model_path
        if model_path is None:
            raise ConfigurationError(
                "VOXCPM2_MODEL_PATH 未設定，無法載入語音模型。"
                "請在 .env 指向模型權重目錄（內含 config.json 與 model.safetensors）。"
            )
        if not Path(model_path).is_dir():
            raise ConfigurationError(
                f"VOXCPM2_MODEL_PATH 指向的目錄不存在：{model_path}"
            )

        for label, env_name, path in (
            ("參考音檔", "VOXCPM2_REFERENCE_WAV", self._settings.reference_wav),
            ("prompt 音檔", "VOXCPM2_PROMPT_WAV", self._settings.prompt_wav),
        ):
            if path is not None and not Path(path).is_file():
                raise ConfigurationError(
                    f"{label}（{env_name}）指向的檔案不存在：{path}"
                )

    @property
    def uses_random_voice(self) -> bool:
        """沒有任何音色來源——每張卡都會是不同的聲音。"""
        return self._settings.uses_random_voice

    # ── 生成 ─────────────────────────────────────────────────────

    async def synthesize(self, text: str) -> bytes:
        """合成一段語音，回傳 WAV bytes。

        Args:
            text: 要唸的文字，取自 `tts_front_text` 或 `tts_back_text`。

        Raises:
            ConfigurationError: 設定有問題（見 `validate()`）。
            ExternalServiceError: 文字為空，或套件生成失敗。
        """
        if not text.strip():
            raise ExternalServiceError("要合成的文字為空，無法生成語音")

        model = await self._ensure_model()
        audio = await asyncio.to_thread(self._generate, model, text)
        return await asyncio.to_thread(self._encode, audio, self._sample_rate(model))

    async def _ensure_model(self) -> Any:
        """取得模型實例，必要時建構。首次載入以鎖保護，避免併發時重複建構。"""
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                self.validate()
                self._warn_if_random_voice()
                self._model = await asyncio.to_thread(self._model_factory)
        return self._model

    def _build_model(self) -> Any:
        """延後匯入並建構 `VoxCPM`。同步且耗時，只在 `to_thread` 中呼叫。"""
        # 套件在建構與生成時都會噴 tqdm 進度條到 stderr，會與本專案的進度顯示
        # 搶同一行。setdefault 保留使用者刻意覆寫的機會
        os.environ.setdefault("TQDM_DISABLE", "1")

        from voxcpm import VoxCPM

        settings = self._settings
        return VoxCPM(
            voxcpm_model_path=str(settings.model_path),
            enable_denoiser=settings.enable_denoiser,
            optimize=settings.optimize,
            device=settings.device or None,
        )

    def _generate(self, model: Any, text: str):  # noqa: ANN202 - np.ndarray，不為此匯入 numpy
        """呼叫套件生成波形。

        參數一律取自設定；`retry_badcase` 交給套件內建，**本專案不再包一層重試**。
        """
        settings = self._settings
        try:
            return model.generate(
                text,
                prompt_wav_path=_as_str(settings.prompt_wav),
                prompt_text=settings.prompt_text or None,
                reference_wav_path=_as_str(settings.reference_wav),
                cfg_value=settings.cfg_value,
                inference_timesteps=settings.inference_timesteps,
                normalize=settings.normalize,
                denoise=settings.denoise,
            )
        except Exception as error:  # noqa: BLE001 - 套件例外型別未公開，一律轉為本專案例外
            raise ExternalServiceError(
                f"VOXCPM2 生成失敗（{type(error).__name__}: {error}）：{text[:40]!r}"
            ) from error

    @staticmethod
    def _sample_rate(model: Any) -> int:
        """取樣率由模型決定（實測 48000），與套件自己的 CLI 取法一致。"""
        rate = getattr(getattr(model, "tts_model", None), "sample_rate", None)
        if not rate:
            raise ExternalServiceError("取不到模型的取樣率（model.tts_model.sample_rate）")
        return int(rate)

    @staticmethod
    def _encode(audio: Any, sample_rate: int) -> bytes:
        """float32 波形 → WAV bytes。"""
        buffer = io.BytesIO()
        try:
            sf.write(buffer, audio, sample_rate, format="WAV", subtype=WAV_SUBTYPE)
        except Exception as error:  # noqa: BLE001 - soundfile 的例外型別隨後端而異
            raise ExternalServiceError(
                f"波形寫入 WAV 失敗（{type(error).__name__}: {error}）"
            ) from error
        data = buffer.getvalue()
        if not data:
            raise ExternalServiceError("生成的音檔內容為空")
        return data

    def _warn_if_random_voice(self) -> None:
        """沒設音色來源時提醒一次——整套牌組每張卡的聲音都會不一樣。"""
        if self._warned_random_voice or not self.uses_random_voice:
            return
        self._warned_random_voice = True
        logger.warning(
            "未設定 VOXCPM2_REFERENCE_WAV 或 VOXCPM2_PROMPT_WAV，"
            "每次生成都是隨機音色——整套牌組的聲音不會一致。"
        )


def _as_str(path: Path | None) -> str | None:
    return str(path) if path is not None else None
