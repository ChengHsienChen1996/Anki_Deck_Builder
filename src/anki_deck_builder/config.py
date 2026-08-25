"""設定層：以 pydantic-settings 載入 `.env`，巢狀分組存取。

設計要點（依 docs/architecture.md〈設定參數化〉）：

1. **零硬編碼**（約束 5）：所有 endpoint、模型名、ComfyUI 節點 ID 與欄位名、路徑，
   一律從這裡取得。
2. **巢狀分組**：`settings.comfyui.nodes.positive_node_id`，避免扁平命名空間。
3. **階段性驗證**：載入時只驗證「變數存在且可轉型」，**不**檢查外部服務可達性、
   也**不**檢查 `COMFYUI_WORKFLOW_PATH` 指向的檔案是否存在——那些留給對應階段，
   否則 Phase 1 會被未設定的 ComfyUI／TTS 擋住。
4. **endpoint 與模型名不在這裡**：它們屬 `agents.yaml` 的 `client.base_url` / `model`，
   由 agent_factory 讀取。本層只管 agent_factory 自己定義的那五個環境變數。

本模組為純 CPU 的設定解析，無 I/O 併發需求，故不採 async（見 docs/coding-style.md）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigurationError

DEFAULT_ENV_FILE = ".env"

IngestMode = Literal["two_stage", "vision_direct"]

# 遮罩用的星號數；同時是「至少要藏住幾個字元」的門檻
_MASK_WIDTH = 4


def _settings_config(prefix: str = "") -> SettingsConfigDict:
    return SettingsConfigDict(
        env_prefix=prefix,
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # VOXCPM2_MODEL_PATH 會對上 model_path 欄位，需解除 pydantic 的 model_ 保留命名空間
        protected_namespaces=(),
    )


def mask_secret(value: SecretStr | str | None, head: int = 3, tail: int = 2) -> str:
    """遮罩金鑰，供 log 與 Web UI 顯示使用。

    >>> mask_secret("sk-abc123")
    'sk-****23'

    被藏起來的字元少於 `_MASK_WIDTH` 個時一律全遮：短字串保留頭尾等於幾乎沒遮，
    反而洩漏內容。
    """
    if value is None:
        return ""
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not raw:
        return ""
    if len(raw) < head + tail + _MASK_WIDTH:
        return "*" * len(raw)
    return f"{raw[:head]}{'*' * _MASK_WIDTH}{raw[-tail:]}"


class AgentFactorySettings(BaseSettings):
    """agent_factory 定義的變數集合，本專案照用，不自行增減。

    endpoint 與模型名寫在 `agents.yaml`，不在此處。
    """

    model_config = _settings_config()

    openai_api_key: SecretStr
    yaml_settings_file: Path
    global_concurrency: int = 6
    rpm: int = 200
    tpm: int = 30000


class IngestSettings(BaseSettings):
    """影像類輸入的路徑選擇。純文字輸入不受此設定影響。"""

    model_config = _settings_config("INGEST_")

    mode: IngestMode = "two_stage"
    #: PDF 逐頁渲染的解析度。過低傷辨識率，過高則 base64 過大且逼近 OCR 模型的像素上限
    pdf_dpi: int = Field(default=200, gt=0, le=600)
    #: 抽取階段每次呼叫最多送幾個非空行。本地模型面對太多條目會退化成壞 JSON
    #: 或只回一張卡；失敗時階段會自動對半再切，此值只是起點
    extract_chunk_lines: int = Field(default=20, gt=0)


class ModelUnloadSettings(BaseSettings):
    """階段間的 VRAM 讓渡。預設關閉——它是最佳化，不是流程的一部分。

    僅對 Ollama 有效（`keep_alive` 是其專屬參數）。endpoint 與模型名不在此宣告，
    一律由 `agents.yaml` 的 agent 物件推得，避免同一份資訊有兩個來源。
    """

    model_config = _settings_config("MODEL_UNLOAD_")

    #: 階段**結束後**卸載自己用的模型。預設關閉——只有要把 VRAM 讓給
    #: 非 Ollama 的消費者（Phase 3 的 ComfyUI）時才需要
    enabled: bool = False
    #: 階段**開始前**卸載其他常駐模型。預設開啟——抽取 20.3 GB 與 OCR 2.2 GB
    #: 在 24 GB 卡上無法共存，前一階段的模型不讓位，下一階段就會卡到逾時
    before_stage: bool = True
    #: 送出卸載請求後，輪詢 /api/ps 確認 VRAM 真的釋放的等待上限
    timeout: float = Field(default=30.0, gt=0)


class PathSettings(BaseSettings):
    """工作目錄。兩者皆為執行期產物，不納入版控。"""

    model_config = _settings_config()

    work_dir: Path = Path("./work")
    output_dir: Path = Path("./output")

    @property
    def cards_csv(self) -> Path:
        """中間 CSV 的預設路徑（CLI `--work` 未指定時採用）。"""
        return self.work_dir / "cards.csv"


class ComfyUINodeSettings(BaseSettings):
    """workflow 的注入點。

    workflow 由使用者自帶、節點結構因人而異，故每個注入點都拆成
    「節點 ID」與「欄位名」兩個變數（約束 5）。
    positive / negative / output 為必要；seed 與 latent 尺寸選填，留空則不注入。
    """

    model_config = _settings_config("COMFYUI_")

    positive_node_id: str = ""
    positive_field: str = "text"
    negative_node_id: str = ""
    negative_field: str = "text"
    seed_node_id: str = ""
    seed_field: str = "seed"
    latent_node_id: str = ""
    width_field: str = "width"
    height_field: str = "height"
    output_node_id: str = ""


class ComfyUISettings(BaseSettings):
    """ComfyUI 連線與生成參數（Phase 3 使用）。"""

    model_config = _settings_config("COMFYUI_")

    base_url: str = "http://127.0.0.1:8188"
    workflow_path: Path = Path("workflows/card_image.json")
    poll_interval: float = 2.0
    timeout: int = 300
    batch_size: int = 4
    #: 16:9 對應記憶引擎的卡片版面（見 prompts/image_prompt_template.md）。
    #: 寬邊停在 768 是因為 SD 1.5 系列的原生解析度是 512，拉到 1024 常出現主體重複
    image_width: int = 768
    image_height: int = 432
    #: 前段為畫質與解剖負向詞，後段為防文字負向詞——正向後綴已從正面約束一次，
    #: 兩邊都要有才擋得乾淨（見 prompts/image_prompt_template.md）
    negative_prompt: str = (
        "lowres, worst quality, low quality, normal quality, jpeg artifacts, blurry, "
        "bad anatomy, bad hands, poorly drawn face, deformed, disfigured, ugly, mutated, "
        "mutated hands, extra fingers, fused fingers, missing fingers, extra digit, "
        "fewer digits, bad proportions, gross proportions, malformed limbs, extra limbs, "
        "missing limbs, extra arms, missing arms, extra legs, missing legs, long neck, "
        "cropped, error, text, letters, words, caption, subtitle, signature, watermark, "
        "username, artist name"
    )
    #: extract 開始前是否請 ComfyUI 釋放 VRAM。預設關閉——它是最佳化，
    #: 且開啟後 ComfyUI 下次生成要重載模型。實測數據見 Phase 3 驗收
    free_before_llm: bool = False
    nodes: ComfyUINodeSettings = Field(default_factory=ComfyUINodeSettings)


class TTSSettings(BaseSettings):
    """VOXCPM2 語音生成（Phase 4 使用）。

    `voxcpm` 是**本機 Python 套件**，推論跑在本行程內，不是 HTTP 服務——
    所以沒有 endpoint、沒有連線逾時，也沒有 language／speed／speaker_id
    這類參數（語言由文字本身決定，音色由參考音檔決定）。
    """

    model_config = _settings_config("VOXCPM2_")

    #: 模型權重目錄。Phase 4 才需要，故此處不設為必填（階段性驗證）
    model_path: Path | None = None
    #: 留空為自動選擇（優先 CUDA）
    device: str = ""

    # ── 音色 ──
    #: Voice Design：以文字描述指定音色，例如
    #: `(A young woman, clear and steady voice, neutral American accent)`。
    #: 單獨使用時完全不需要參考音檔；與 reference_wav 併用時退為風格控制
    #: （README 的 Controllable Voice Cloning）
    voice_description: str = ""
    #: voice cloning 的參考音檔。留空則每次生成都是隨機音色
    reference_wav: Path | None = None
    #: continuation 模式，與 prompt_text 必須成對
    prompt_wav: Path | None = None
    prompt_text: str = ""

    # ── 生成參數 ──
    cfg_value: float = 2.0
    inference_timesteps: int = 10
    normalize: bool = False
    #: 降噪需 ModelScope 的 zipenhancer 模型，會觸發下載，預設關閉
    enable_denoiser: bool = False
    denoise: bool = False
    #: torch.compile 最佳化，除錯時可關閉
    optimize: bool = True
    #: 本行程內的 GPU 推論本就序列化，設 1 以外的值不會更快
    concurrency: int = 1

    # ── 響度正規化 ──
    #: 統一每段語音的輸出音量。模型逐段生成的音量本來就飄（308 張卡實測
    #: gated RMS 全距 83 dB），關掉就是模型原樣輸出。
    #: **與上面的 `normalize` 無關**——那個是套件的文字正規化
    loudness_normalize: bool = True
    #: 目標響度（gated RMS，dBFS）。-20 是語音素材常見的落點；
    #: 調高會更大聲，但受峰值上限牽制，過高只會讓每段都貼齊上限而失去動態
    loudness_target_dbfs: float = -20.0
    #: 峰值上限（dBFS）。放大到目標會削波時改以此為準。
    #: 留 1 dB 餘裕給 PCM_16 量化與播放端的重採樣
    loudness_peak_dbfs: float = -1.0
    #: 背面語音要不要連譯文一起唸。關閉時唸 `tts_back_text`（只有原文），
    #: 開啟時改唸 `example`（原文＋譯文）——選的是欄位，不是切字串
    speak_translation: bool = False

    @field_validator("model_path", "reference_wav", "prompt_wav", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """`.env` 裡「有這一行但留空」等同未設定。

        沒有這一層時 pydantic 會把空字串轉成 `Path('.')`——一個存在的目錄，
        既不是 `None` 也不是有效的音檔。照 `.env.example` 原樣複製就會踩到：
        `VOXCPM2_PROMPT_WAV=` 讓成對性驗證誤判、`VOXCPM2_REFERENCE_WAV=` 讓
        `uses_random_voice` 誤判為否。
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _prompt_pair_must_be_complete(self) -> TTSSettings:
        """continuation 模式的兩個變數必須同時提供，缺一方 voxcpm 會直接拋錯。"""
        if (self.prompt_wav is None) == (not self.prompt_text):
            return self
        raise ValueError(
            "VOXCPM2_PROMPT_WAV 與 VOXCPM2_PROMPT_TEXT 必須同時提供或同時留空"
        )

    @property
    def uses_random_voice(self) -> bool:
        """未指定任何音色來源時為真——整套牌組的聲音不會一致。

        三種來源任一個都算指定：文字描述（Voice Design）、參考音檔（cloning）、
        prompt 音檔（continuation）。
        """
        return (
            not self.voice_description.strip()
            and self.reference_wav is None
            and self.prompt_wav is None
        )


class Settings(BaseModel):
    """全專案設定的聚合根，各分組獨立載入後組裝。"""

    agent_factory: AgentFactorySettings
    ingest: IngestSettings
    model_unload: ModelUnloadSettings
    paths: PathSettings
    comfyui: ComfyUISettings
    tts: TTSSettings

    def masked_summary(self) -> dict[str, str]:
        """供 log 與 Web UI 顯示的摘要，金鑰一律遮罩。"""
        return {
            "OPENAI_API_KEY": mask_secret(self.agent_factory.openai_api_key),
            "YAML_SETTINGS_FILE": str(self.agent_factory.yaml_settings_file),
            "GLOBAL_CONCURRENCY": str(self.agent_factory.global_concurrency),
            "INGEST_MODE": self.ingest.mode,
            "MODEL_UNLOAD_ENABLED": str(self.model_unload.enabled),
            "MODEL_UNLOAD_BEFORE_STAGE": str(self.model_unload.before_stage),
            "WORK_DIR": str(self.paths.work_dir),
            "OUTPUT_DIR": str(self.paths.output_dir),
            "COMFYUI_BASE_URL": self.comfyui.base_url,
            "VOXCPM2_MODEL_PATH": str(self.tts.model_path or ""),
            "VOXCPM2_REFERENCE_WAV": str(self.tts.reference_wav or "(隨機音色)"),
        }


def _env_var_name(model_cls: type[BaseSettings], field_name: str) -> str:
    prefix = model_cls.model_config.get("env_prefix", "") or ""
    return f"{prefix}{field_name}".upper()


def _as_configuration_error(
    exc: ValidationError, model_cls: type[BaseSettings]
) -> ConfigurationError:
    """把 pydantic 的驗證錯誤轉成指名環境變數的 ConfigurationError。"""
    messages: list[str] = []
    for err in exc.errors():
        if not err["loc"]:
            # model_validator 的錯誤不對應單一欄位，訊息本身已寫明變數名
            messages.append(err["msg"].removeprefix("Value error, "))
            continue
        field = str(err["loc"][0])
        name = _env_var_name(model_cls, field)
        if err["type"] == "missing":
            messages.append(f"缺少必填環境變數 {name}")
        else:
            messages.append(
                f"環境變數 {name} 的值無效（收到 {err.get('input')!r}）：{err['msg']}"
            )
    return ConfigurationError("；".join(messages))


def load_settings(env_file: str | Path | None = DEFAULT_ENV_FILE) -> Settings:
    """載入設定。

    Args:
        env_file: `.env` 路徑；傳 `None` 表示只讀行程環境變數（測試用）。

    Raises:
        ConfigurationError: 必填變數缺漏或型別轉換失敗，訊息含變數名。
    """
    groups: list[type[BaseSettings]] = [
        AgentFactorySettings,
        IngestSettings,
        PathSettings,
        ComfyUINodeSettings,
        ComfyUISettings,
        TTSSettings,
        ModelUnloadSettings,
    ]
    loaded: dict[type[BaseSettings], BaseSettings] = {}
    for cls in groups:
        kwargs = {}
        if cls is ComfyUISettings:
            kwargs["nodes"] = loaded[ComfyUINodeSettings]
        try:
            loaded[cls] = cls(_env_file=env_file, **kwargs)
        except ValidationError as exc:
            raise _as_configuration_error(exc, cls) from exc

    return Settings(
        agent_factory=loaded[AgentFactorySettings],
        ingest=loaded[IngestSettings],
        model_unload=loaded[ModelUnloadSettings],
        paths=loaded[PathSettings],
        comfyui=loaded[ComfyUISettings],
        tts=loaded[TTSSettings],
    )
