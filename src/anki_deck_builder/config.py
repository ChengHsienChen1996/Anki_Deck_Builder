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

from pydantic import BaseModel, Field, SecretStr, ValidationError
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

    待確認：`speaker_id` 的實際型別（字串 id／整數索引／模型檔路徑）待介面規格確認，
    目前以字串承接，規格明確後再收斂。
    """

    model_config = _settings_config("VOXCPM2_")

    base_url: str = "http://127.0.0.1:9880"
    speaker_id: str = ""
    language: str = "ja"
    speed: float = 1.0
    timeout: int = 120
    concurrency: int = 2


class Settings(BaseModel):
    """全專案設定的聚合根，各分組獨立載入後組裝。"""

    agent_factory: AgentFactorySettings
    ingest: IngestSettings
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
            "WORK_DIR": str(self.paths.work_dir),
            "OUTPUT_DIR": str(self.paths.output_dir),
            "COMFYUI_BASE_URL": self.comfyui.base_url,
            "VOXCPM2_BASE_URL": self.tts.base_url,
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
        field = str(err["loc"][0]) if err["loc"] else "?"
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
        paths=loaded[PathSettings],
        comfyui=loaded[ComfyUISettings],
        tts=loaded[TTSSettings],
    )
