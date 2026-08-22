"""OCRClient 測試骨架 —— **AI 撰寫但不執行，需人工驗證**。

理由同 `test_llm_client.py`：`agents.yaml` 的 endpoint 可指向線上付費服務或本地模型，
執行時不易區分；本地模型載入佔 VRAM，還會與 ComfyUI 搶資源。因此整個模組標記
`@pytest.mark.manual`，`uv run pytest` 預設不會執行。

人工驗證時：

    uv run pytest -m manual tests/clients/test_ocr_client.py -v

mock 對象為 `LimitAgentRunner.run`，不觸及任何真實推理。

**例外**：`encode_image_b64()` 與 `_fit_pixels()` 是純本地影像運算、不呼叫任何服務，
其測試放在 `tests/clients/test_image_encoding.py`，**照常自動執行**。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from anki_deck_builder.clients.ocr_client import OCR_AGENT_NAME, OCRClient, build_image_input
from anki_deck_builder.exceptions import ConfigurationError, ExternalServiceError

pytestmark = [pytest.mark.manual, pytest.mark.asyncio]

MODULE = "anki_deck_builder.clients.ocr_client"

#: 取材自 tests/fixtures/ocr_raw/page_01.txt，貼近真實形狀
SAMPLE_TEXT = "□増大\nぞうだい\n[名・自他サ] 增多，增大\n予算が増大する／預算大幅增加。"


@pytest.fixture
def fake_runner():
    """把 LimitAgentRunner 換成 mock，攔在真正發出請求之前。"""
    with patch(f"{MODULE}.LimitAgentRunner") as runner_cls:
        instance = runner_cls.return_value
        instance.run = AsyncMock()
        yield instance


@pytest.fixture
def fake_factory():
    with patch(f"{MODULE}.AgentFactory") as factory_cls:
        factory_cls.create_factory_from_yaml.return_value.get_agent_by_name.return_value = (
            SimpleNamespace(name=OCR_AGENT_NAME)
        )
        yield factory_cls


# ── 正常路徑 ────────────────────────────────────────────────────


async def test_recognize_returns_text(fake_factory, fake_runner) -> None:
    fake_runner.run.return_value = SimpleNamespace(final_output=SAMPLE_TEXT)

    text = await OCRClient("agents.yaml").recognize("ZmFrZQ==")

    assert text == SAMPLE_TEXT


async def test_recognize_strips_surrounding_whitespace(fake_factory, fake_runner) -> None:
    fake_runner.run.return_value = SimpleNamespace(final_output=f"\n\n{SAMPLE_TEXT}\n  ")

    assert await OCRClient("agents.yaml").recognize("ZmFrZQ==") == SAMPLE_TEXT


async def test_image_is_sent_as_data_url_content_block(fake_factory, fake_runner) -> None:
    """格式須與 submodule 的參考實作一致，否則模型收不到影像。"""
    fake_runner.run.return_value = SimpleNamespace(final_output=SAMPLE_TEXT)

    await OCRClient("agents.yaml").recognize("ZmFrZQ==")

    sent = fake_runner.run.await_args.kwargs["input_"]
    block = sent[0]["content"][0]
    assert block["type"] == "input_image"
    assert block["image_url"].startswith("data:image/jpeg;base64,ZmFrZQ==")


async def test_task_prompt_is_not_duplicated_in_input(fake_factory, fake_runner) -> None:
    """任務前綴由 agents.yaml 的 instruction 帶入；GLM-OCR 多收一份自擬文字會退化。"""
    fake_runner.run.return_value = SimpleNamespace(final_output=SAMPLE_TEXT)

    await OCRClient("agents.yaml").recognize("ZmFrZQ==")

    sent = fake_runner.run.await_args.kwargs["input_"]
    assert len(sent) == 1
    assert all(item["type"] == "input_image" for item in sent[0]["content"])


async def test_runner_is_reused_across_calls(fake_factory, fake_runner) -> None:
    """runner 持有速率限制器，每次呼叫重建會讓限流形同虛設。"""
    fake_runner.run.return_value = SimpleNamespace(final_output=SAMPLE_TEXT)
    client = OCRClient("agents.yaml")

    await client.recognize("ZmFrZQ==")
    await client.recognize("ZmFrZQ==")

    assert fake_runner.run.await_count == 2
    factory = fake_factory.create_factory_from_yaml.return_value
    assert factory.get_agent_by_name.call_count == 1


# ── 錯誤轉換 ────────────────────────────────────────────────────


async def test_empty_image_raises_before_calling(fake_factory, fake_runner) -> None:
    with pytest.raises(ExternalServiceError, match="影像內容為空"):
        await OCRClient("agents.yaml").recognize("")

    fake_runner.run.assert_not_awaited()


async def test_empty_response_raises(fake_factory, fake_runner) -> None:
    """空回應多半是 max_tokens 被吃光，訊息要指出這點。"""
    fake_runner.run.return_value = SimpleNamespace(final_output="   ")

    with pytest.raises(ExternalServiceError, match="空結果"):
        await OCRClient("agents.yaml").recognize("ZmFrZQ==")


async def test_timeout_becomes_external_service_error(fake_factory, fake_runner) -> None:
    fake_runner.run.side_effect = TimeoutError("timed out")

    with pytest.raises(ExternalServiceError, match="TimeoutError"):
        await OCRClient("agents.yaml").recognize("ZmFrZQ==")


async def test_auth_failure_becomes_external_service_error(fake_factory, fake_runner) -> None:
    fake_runner.run.side_effect = PermissionError("invalid api key")

    with pytest.raises(ExternalServiceError, match="辨識失敗"):
        await OCRClient("agents.yaml").recognize("ZmFrZQ==")


async def test_unknown_agent_becomes_configuration_error(fake_factory) -> None:
    """模型未登錄／agent 名稱寫錯時，KeyError 要轉成指名問題的專案例外。"""
    factory = fake_factory.create_factory_from_yaml.return_value
    factory.get_agent_by_name.side_effect = KeyError(OCR_AGENT_NAME)

    with pytest.raises(ConfigurationError, match="找不到 agent"):
        await OCRClient("agents.yaml").recognize("ZmFrZQ==")


async def test_yaml_load_failure_becomes_configuration_error(fake_factory) -> None:
    fake_factory.create_factory_from_yaml.side_effect = FileNotFoundError("agents.yaml")

    with pytest.raises(ConfigurationError, match="載入 agents.yaml 失敗"):
        await OCRClient("agents.yaml").recognize("ZmFrZQ==")


async def test_project_exceptions_pass_through(fake_factory, fake_runner) -> None:
    """專案例外不再包一層，否則錯誤訊息會層層疊加。"""
    fake_runner.run.side_effect = ConfigurationError("原始訊息")

    with pytest.raises(ConfigurationError, match="原始訊息"):
        await OCRClient("agents.yaml").recognize("ZmFrZQ==")


# ── 輸入組裝（純函式，但仍留在此模組一併人工驗證）────────────────


def test_build_image_input_accepts_custom_mime() -> None:
    sent = build_image_input("ZmFrZQ==", mime="image/png")
    assert sent[0]["content"][0]["image_url"].startswith("data:image/png;base64,")
