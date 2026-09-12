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

## 三種音色來源可以並存

| 來源 | 設定 | 說明 |
|------|------|------|
| Voice Design | `VOXCPM2_VOICE_DESCRIPTION` | 文字描述，接在每段文字前面；不需音檔 |
| voice cloning | `VOXCPM2_REFERENCE_WAV` | 參考音檔，以 ref_audio token 隔離 |
| continuation | `VOXCPM2_PROMPT_WAV` + `_PROMPT_TEXT` | 需成對；與 reference 同檔即最高保真 |

**彼此不互斥**：描述單獨使用就是 Voice Design；與參考音檔併用時，描述退為
風格控制（README 的 Controllable Voice Cloning）。要換路線只需改設定，
不必動程式碼。

## 不自行重試

套件內建 `retry_badcase`（預設開、最多 3 次、以音長／文字長度比判斷）。
再包一層只會讓失敗的案例多花三倍時間。

## 輸出響度統一

模型逐段生成的音量本來就飄——308 張卡的 616 段實測，閘門後 RMS 全距 83 dB，
播起來就是「時大時小」。寫檔前一律做響度正規化（`normalize_loudness()`）：
以閘門後的 RMS 對齊 `VOXCPM2_LOUDNESS_TARGET_DBFS`，再以
`VOXCPM2_LOUDNESS_PEAK_DBFS` 封住峰值，兩者取較小的增益。

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

import numpy as np
import soundfile as sf

from ..config import TTSSettings
from ..exceptions import ConfigurationError, ExternalServiceError

logger = logging.getLogger(__name__)

#: 寫檔的取樣格式。模型輸出 float32，直接寫成 WAV 會是 IEEE float——檔案大一倍，
#: 且不是每個瀏覽器都播得動。記憶引擎在瀏覽器裡播放，PCM_16 是最保險的選擇
WAV_SUBTYPE = "PCM_16"

#: 響度正規化的放大上限。生成失敗的「幾乎無聲」音檔 gated RMS 可低到 -90 dBFS，
#: 不設上限就會被放大 70 dB——聽到的是背景噪音，不是人聲。
#: 觸頂時記一則 warning，那通常代表這一段該重生成
MAX_GAIN_DB = 20.0

#: 量響度的框長（毫秒）。50 ms 約一個音節：短到跟得上語音起伏，長到不受單一取樣左右
_FRAME_MS = 50.0

#: 閘門（dB）。只有落在最大框以下 30 dB 內的框才算「有聲音」。
#: 不閘門的話，有停頓的例句會因為停頓被平均進去而顯得比單字小聲——
#: 那正是「時大時小」的主因之一
_GATE_DB = 30.0


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
                _with_voice_description(text, settings.voice_description),
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

    def _encode(self, audio: Any, sample_rate: int) -> bytes:
        """float32 波形 → WAV bytes，途中統一響度。"""
        settings = self._settings
        if settings.loudness_normalize:
            audio, gain_db, hit_cap = normalize_loudness(
                audio,
                sample_rate,
                target_dbfs=settings.loudness_target_dbfs,
                peak_dbfs=settings.loudness_peak_dbfs,
            )
            if hit_cap:
                logger.warning(
                    "音檔偏小聲：已放大 %.1f dB（上限）仍未達目標 %.1f dBFS，"
                    "這一段可能生成失敗，建議重跑該張卡",
                    gain_db,
                    settings.loudness_target_dbfs,
                )
            else:
                logger.debug("響度正規化：%+.1f dB", gain_db)
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
            "未設定 VOXCPM2_VOICE_DESCRIPTION、VOXCPM2_REFERENCE_WAV 或 "
            "VOXCPM2_PROMPT_WAV，每次生成都是隨機音色——整套牌組的聲音不會一致。"
        )


def release_gpu_cache() -> bool:
    """把 PyTorch 快取配置器持有的 VRAM 還給驅動程式。

    VOXCPM2 是**行程內**的模型（快取在 `VoxCPMClient._model`），沒有可以打的
    「請你讓位」端點。丟掉實例之後，PyTorch 的 caching allocator 仍會抓著那塊
    顯存不放，要 `empty_cache()` 才會真的釋出。

    ## 為什麼要先 `gc.collect()`（2026-09-12 實測，這行不能拿掉）

    **`empty_cache()` 只還得了「已經沒人用」的區塊。** 上一輪的 `VoxCPMClient`
    帶著參照循環（client ↔ stage ↔ settings），單靠 refcount 不會立刻回收——
    在循環回收器跑到之前，那 5.4 GB 的權重在 PyTorch 眼中仍是**活著的張量**，
    `empty_cache()` 一個位元組都還不了。

    後果在長駐的 Web UI 上是致命的：每按一次語音按鈕就多載入一份模型而舊的不走，
    實測 `allocated` 5432 → 10856 MB，**第三次必定 CUDA OOM**（22.59 GiB／24 GB，
    與使用者實際遇到的數字一致）。CLI 感覺不到，因為每個指令是獨立行程。

    加上 `gc.collect()` 之後，同樣三輪的 `allocated` 每輪都回到 8 MB。

    **順序不可顛倒**：先回收才有空閒區塊可還。

    **只在 torch 已經被匯入時才動作。** 全新的 CLI 行程根本沒載過 torch，
    為了清一塊空的快取而匯入它要多花好幾秒（見本模組開頭對延後匯入的說明）；
    而真正需要這個函式的是**長駐的 Web UI 行程**——在那裡跑完 audio 之後，
    VOXCPM2 的約 7.5 GB 會一直留著，接下來的 image 階段就少了那麼多可用顯存。

    Returns:
        是否真的做了釋放（torch 未載入或沒有 CUDA 時為 `False`）。
    """
    import gc
    import sys

    torch = sys.modules.get("torch")
    if torch is None:
        return False
    try:
        if not torch.cuda.is_available():
            return False
        # 先把上一輪的模型從參照循環裡收掉，它才會變成 empty_cache() 還得動的區塊
        gc.collect()
        torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 - 讓渡是最佳化，失敗不該擋下任何階段
        return False
    return True


def normalize_loudness(
    audio: Any,
    sample_rate: int,
    *,
    target_dbfs: float,
    peak_dbfs: float,
    max_gain_db: float = MAX_GAIN_DB,
) -> tuple[Any, float, bool]:
    """把一段波形調到統一響度，回傳 `(波形, 實際增益 dB, 是否觸及放大上限)`。

    增益取兩者的**較小值**：
    「調到目標響度所需的倍率」與「不讓峰值超過上限的倍率」。
    因此小聲的被拉上來、大聲的被壓下去，且任何一段都不會削波——
    這就是「統一以最大聲輸出」的實際定義。

    響度用**閘門後的 RMS**，不是峰值：峰值只反映最尖的那一個取樣，
    一聲氣音就能讓整段被判定為「已經夠大聲」，聽感卻還是小聲的。

    衰減不設下限（大聲的一律壓到目標），放大則以 `max_gain_db` 封頂——
    幾乎無聲的失敗音檔不該被放大成噪音。
    """
    samples = np.asarray(audio, dtype=np.float32)
    mono = samples.mean(axis=1) if samples.ndim > 1 else samples

    rms, peak = _measure(mono, sample_rate)
    if rms <= 0.0 or peak <= 0.0:
        return samples, 0.0, False  # 全靜音，放大它沒有意義

    gain = min(_from_db(target_dbfs) / rms, _from_db(peak_dbfs) / peak)
    max_gain = _from_db(max_gain_db)
    hit_cap = gain > max_gain
    gain = min(gain, max_gain)
    return samples * gain, _to_db(gain), hit_cap


def _measure(mono: Any, sample_rate: int) -> tuple[float, float]:
    """回傳 `(閘門後的 RMS, 峰值)`，皆為線性振幅。

    做法比照 LUFS 的閘門，但省掉 K 加權——本專案要的是「彼此一致」，
    不是與播出標準對齊，多一層濾波器只會多一個要維護的東西。
    """
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak <= 0.0:
        return 0.0, 0.0

    frame = max(1, int(sample_rate * _FRAME_MS / 1000))
    usable = mono.size - mono.size % frame
    if usable < frame:  # 比一個框還短，整段一起算
        return float(np.sqrt(np.mean(mono.astype(np.float64) ** 2))), peak

    frames = mono[:usable].astype(np.float64).reshape(-1, frame)
    power = np.mean(frames**2, axis=1)
    threshold = power.max() * _from_db(-_GATE_DB) ** 2
    voiced = power[power >= threshold]
    return float(np.sqrt(voiced.mean())), peak


def _from_db(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def _to_db(ratio: float) -> float:
    return float(20.0 * np.log10(max(ratio, 1e-9)))


def _with_voice_description(text: str, description: str) -> str:
    """把 Voice Design 的描述接在文字前面。

    README 的格式是「把括號描述放在 `text` 開頭，後面直接接要唸的內容」，
    中間不加空白——描述本身已經以括號收尾。
    """
    description = description.strip()
    return f"{description}{text}" if description else text


def _as_str(path: Path | None) -> str | None:
    return str(path) if path is not None else None
