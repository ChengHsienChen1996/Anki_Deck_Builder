"""LLMClient 測試骨架 —— **AI 撰寫但不執行，需人工驗證**。

依 docs/testing-strategy.md 與 docs/llm-integration.md：`agents.yaml` 的 endpoint
可指向線上付費服務或本地模型，執行時不易區分；本地模型（31B q4）載入成本高、佔 VRAM，
還會與 ComfyUI 搶資源。兩條規則都導向同一結論——寫 mock 骨架但不跑。

因此整個模組標記 `@pytest.mark.manual`，`uv run pytest` 預設不會執行。
人工驗證時：

    uv run pytest -m manual tests/clients/test_llm_client.py -v

mock 對象為 `LimitAgentRunner.run`，不觸及任何真實推理。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anki_deck_builder.clients.llm_client import LLMClient
from anki_deck_builder.exceptions import ConfigurationError, ExternalServiceError
from anki_deck_builder.schemas import ExtractedCard, ExtractOutput

pytestmark = [pytest.mark.manual, pytest.mark.asyncio]

MODULE = "anki_deck_builder.clients.llm_client"


def _extract_output() -> ExtractOutput:
    """mock 回應的內容取材自 tests/fixtures/expected_cards.csv，貼近真實形狀。"""
    return ExtractOutput(
        cards=[
            ExtractedCard(
                card_id="fx_n2_001",
                deck="日語::N2::動詞",
                front="属する",
                back="屬於，歸於；隸屬，附屬",
                hint="自サ",
                example="虎はネコ科に属する。\\n老虎屬於貓科。",
                tags="n2 verb suru-verb",
                category="vocabulary",
                difficulty=3,
                source="單字書 p.333",
                reading="ぞくする",
                image_prompt="a tiger standing among a family of cats, no text",
                tts_front_text="ぞくする",
                tts_back_text="虎はネコ科に属する。",
            )
        ]
    )


@pytest.fixture
def fake_runner():
    """把 LimitAgentRunner 換成 mock，攔在真正發出請求之前。"""
    with patch(f"{MODULE}.LimitAgentRunner") as runner_cls:
        instance = runner_cls.return_value
        instance.run = AsyncMock()
        yield instance


@pytest.fixture
def fake_factory():
    with patch(f"{MODULE}.create_agent_factory") as create:
        factory = MagicMock()
        factory.get_agent_by_name.return_value = MagicMock(name="ExtractAgent")
        create.return_value = factory
        yield create, factory


# ── 正常流程 ─────────────────────────────────────────────────────


async def test_returns_parsed_structured_output(fake_factory, fake_runner) -> None:
    """回傳值由 agent_factory 依 output_schema 解析，本層原樣往上傳。"""
    expected = _extract_output()
    fake_runner.run.return_value = SimpleNamespace(final_output=expected)
    client = LLMClient()

    result = await client.run_agent("ExtractAgent", "□ 属する（ぞくする）［自サ］")

    assert result is expected
    assert isinstance(result, ExtractOutput)
    assert result.cards[0].reading == "ぞくする"
    fake_runner.run.assert_awaited_once_with(input_="□ 属する（ぞくする）［自サ］")


async def test_accepts_image_message_list(fake_factory, fake_runner) -> None:
    """vision_direct（Phase 2）送 message list，介面不需改動。"""
    fake_runner.run.return_value = SimpleNamespace(final_output=_extract_output())
    model_input = [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": "data:image/jpeg;base64,AAAA",
                }
            ],
        }
    ]
    client = LLMClient()

    await client.run_agent("VisionExtractAgent", model_input)

    fake_runner.run.assert_awaited_once_with(input_=model_input)


async def test_factory_and_runner_are_reused(fake_factory, fake_runner) -> None:
    """factory 持有全部 agent 設定、runner 持有速率限制器，重建會讓限流失效。"""
    create, factory = fake_factory
    fake_runner.run.return_value = SimpleNamespace(final_output=_extract_output())
    client = LLMClient()

    for _ in range(3):
        await client.run_agent("ExtractAgent", "text")

    assert create.call_count == 1
    assert factory.get_agent_by_name.call_count == 1
    assert fake_runner.run.await_count == 3


async def test_yaml_path_overrides_environment_variable(fake_runner, tmp_path) -> None:
    with patch(f"{MODULE}.AgentFactory") as factory_cls:
        factory_cls.create_factory_from_yaml.return_value.get_agent_by_name.return_value = (
            MagicMock()
        )
        fake_runner.run.return_value = SimpleNamespace(final_output=_extract_output())
        yaml_path = tmp_path / "agents.yaml"

        await LLMClient(yaml_path).run_agent("ExtractAgent", "text")

        factory_cls.create_factory_from_yaml.assert_called_once_with(yaml_path)


# ── 設定錯誤 ─────────────────────────────────────────────────────


async def test_unknown_agent_raises_configuration_error(fake_factory) -> None:
    """agent 名稱查無屬設定問題，不是服務故障。"""
    _, factory = fake_factory
    factory.get_agent_by_name.side_effect = KeyError("ExtractAgent")

    with pytest.raises(ConfigurationError, match="找不到 agent"):
        await LLMClient().run_agent("ExtractAgent", "text")


async def test_yaml_load_failure_raises_configuration_error() -> None:
    with patch(f"{MODULE}.create_agent_factory", side_effect=FileNotFoundError("agents.yaml")):
        with pytest.raises(ConfigurationError, match="載入 agents.yaml 失敗"):
            await LLMClient().run_agent("ExtractAgent", "text")


# ── 外部服務錯誤 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "hint"),
    [
        (TimeoutError("read timeout"), "TimeoutError"),
        (RuntimeError("429 Too Many Requests"), "429"),
        (ConnectionError("connection refused"), "ConnectionError"),
        (ValueError("預扣量超過該模型的 TPM"), "TPM"),
    ],
)
async def test_call_failures_become_external_service_error(
    fake_factory, fake_runner, exc: Exception, hint: str
) -> None:
    """逾時、速率限制、連線失敗一律轉為專案例外，上層不接觸第三方例外型別。"""
    fake_runner.run.side_effect = exc

    with pytest.raises(ExternalServiceError) as excinfo:
        await LLMClient().run_agent("ExtractAgent", "text")

    message = str(excinfo.value)
    assert "ExtractAgent" in message
    assert hint in message


async def test_plain_text_agents_return_strings(fake_factory, fake_runner) -> None:
    """未宣告 output_schema 的 agent（補釋義、OCR）回傳純文字，這是預期行為。

    型別是否符合該階段的期待，由呼叫端檢查——`ExtractStage._call` 拿到字串時
    仍會拒絕，因為抽取一定要 structured output。
    """
    fake_runner.run.return_value = SimpleNamespace(final_output="這是一段純文字")

    assert await LLMClient().run_agent("EnrichAgent", "text") == "這是一段純文字"


async def test_unexpected_type_is_rejected(fake_factory, fake_runner) -> None:
    """既不是 model 也不是字串，代表 agent_factory 的行為與預期不符。"""
    fake_runner.run.return_value = SimpleNamespace(final_output={"unexpected": True})

    with pytest.raises(ExternalServiceError, match="非預期的型別"):
        await LLMClient().run_agent("ExtractAgent", "text")


async def test_project_errors_are_not_double_wrapped(fake_factory, fake_runner) -> None:
    fake_runner.run.side_effect = ExternalServiceError("原始訊息")

    with pytest.raises(ExternalServiceError, match="原始訊息"):
        await LLMClient().run_agent("ExtractAgent", "text")


# ── 不該發生的事 ─────────────────────────────────────────────────


async def test_client_does_not_implement_rate_limiting(fake_factory, fake_runner) -> None:
    """速率限制屬 LimitAgentRunner.run() 內建，本層不得自行重試或節流。"""
    fake_runner.run.side_effect = RuntimeError("429 Too Many Requests")

    with pytest.raises(ExternalServiceError):
        await LLMClient().run_agent("ExtractAgent", "text")

    assert fake_runner.run.await_count == 1, "本層不應自行重試"
