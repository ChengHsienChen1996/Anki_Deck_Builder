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

from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator
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
    image_width: int = 1024
    image_height: int = 576
    negative_prompt: str = (
        "text, watermark, signature, letters, words, caption, subtitle"
    )
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
        """未指定任何音色來源時為真——整套牌組的聲音不會一致。"""
        return self.reference_wav is None and self.prompt_wav is None


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
