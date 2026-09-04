"""audio 階段單元測試。

狀態流轉、兩側隔離性、檔名與相對路徑回填等純邏輯**可執行**；
TTS 呼叫一律以假 client 替換（依 Protocol 注入），不載入任何模型。
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from anki_deck_builder.exceptions import ConfigurationError, ExternalServiceError
from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.stages import get_stage
from anki_deck_builder.stages.audio import (
    AudioBackStage,
    AudioFrontStage,
    speech_text,
    stages_for_side,
)
from anki_deck_builder.state import CardStore


def _tiny_wav() -> bytes:
    """真的能被 soundfile 解開的 0.1 秒靜音 WAV（同理，轉檔會真的解開它）。"""
    from io import BytesIO

    import numpy as np
    import soundfile as sf

    buffer = BytesIO()
    sf.write(buffer, np.zeros(4800, dtype="float32"), 48000, format="WAV")
    return buffer.getvalue()


WAV = _tiny_wav()


class FakeTTSClient:
    """記錄每次呼叫，可指定回應或錯誤。"""

    def __init__(self, content: bytes = WAV, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[str] = []
        self.validated = 0

    def validate(self) -> None:
        self.validated += 1

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return self.content


class RejectingClient:
    """`validate()` 就失敗——用於驗證「載入模型前擋下」。"""

    def validate(self) -> None:
        raise ConfigurationError("VOXCPM2_MODEL_PATH 指向的目錄不存在：/nope")

    async def synthesize(self, text: str) -> bytes:  # pragma: no cover
        raise AssertionError("設定錯誤時不該送出任何合成請求")


def card(card_id: str = "ja_001", **kwargs) -> CardRow:
    values = {
        "card_id": card_id,
        "front": "属する",
        "back": "屬於",
        "tts_front_text": "属する",
        "tts_back_text": "虎はネコ科に属する。",
    }
    values.update(kwargs)
    return CardRow(**values)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path / "work" / "cards.csv")


async def run_side(store: CardStore, rows: list[CardRow], stage_cls, client, **kwargs):
    await store.write(rows)
    result = await stage_cls(client, **kwargs).run(store)
    return result, await store.read()


# ── 註冊與 side 對應 ─────────────────────────────────────────────


def test_registered_as_two_independent_stages() -> None:
    """狀態層沒有 "audio" 這個階段名，兩側各自註冊。"""
    assert get_stage("audio_front") is AudioFrontStage
    assert get_stage("audio_back") is AudioBackStage
    assert AudioFrontStage.name == "audio_front"
    assert AudioBackStage.name == "audio_back"


def test_plain_audio_is_not_a_stage_name() -> None:
    with pytest.raises(KeyError):
        get_stage("audio")


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        ("front", [AudioFrontStage]),
        ("back", [AudioBackStage]),
        ("both", [AudioFrontStage, AudioBackStage]),
    ],
)
def test_stages_for_side(side: str, expected: list) -> None:
    assert stages_for_side(side) == expected


def test_stages_for_side_rejects_unknown() -> None:
    with pytest.raises(KeyError, match="未知的 side"):
        stages_for_side("middle")


# ── 正常流程 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_front_generates_and_fills_relative_path(
    store: CardStore, tmp_path: Path
) -> None:
    client = FakeTTSClient()

    result, rows = await run_side(store, [card()], AudioFrontStage, client)

    assert result.succeeded == 1
    assert client.calls == ["属する"]
    assert rows[0].audio_front == "media/audio/ja_001_front.mp3"
    assert rows[0].audio_front_status is StageStatus.DONE
    assert (tmp_path / "work" / "media" / "audio" / "ja_001_front.mp3").stat().st_size > 0


@pytest.mark.asyncio
async def test_back_uses_the_example_sentence(store: CardStore, tmp_path: Path) -> None:
    client = FakeTTSClient()

    _, rows = await run_side(store, [card()], AudioBackStage, client)

    assert client.calls == ["虎はネコ科に属する。"]
    assert rows[0].audio_back == "media/audio/ja_001_back.mp3"
    assert (tmp_path / "work" / "media" / "audio" / "ja_001_back.mp3").is_file()


@pytest.mark.asyncio
async def test_media_root_follows_the_csv_not_the_work_dir(tmp_path: Path) -> None:
    """`pack` 以中間 CSV 所在目錄為 media_root，本階段必須落在同一處。"""
    store = CardStore(tmp_path / "別處" / "cards.csv")

    await run_side(store, [card()], AudioFrontStage, FakeTTSClient())

    assert (tmp_path / "別處" / "media" / "audio" / "ja_001_front.mp3").is_file()


@pytest.mark.asyncio
async def test_validate_runs_once_before_synthesizing(store: CardStore) -> None:
    client = FakeTTSClient()

    await run_side(store, [card("a"), card("b")], AudioFrontStage, client)

    assert client.validated == 1
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_configuration_error_stops_before_any_request(store: CardStore) -> None:
    """模型載入要 77 秒，設定錯誤要在那之前擋下。"""
    await store.write([card()])

    with pytest.raises(ConfigurationError, match="VOXCPM2_MODEL_PATH"):
        await AudioFrontStage(RejectingClient()).run(store)

    assert (await store.read())[0].audio_front_status is StageStatus.PENDING


# ── 兩側隔離 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_front_run_does_not_touch_back_status(
    store: CardStore, tmp_path: Path
) -> None:
    """`--side front` 執行後 `audio_back_status` 必須保持原值不變。"""
    result, rows = await run_side(store, [card()], AudioFrontStage, FakeTTSClient())

    assert result.succeeded == 1
    assert rows[0].audio_front_status is StageStatus.DONE
    assert rows[0].audio_back_status is StageStatus.PENDING
    assert rows[0].audio_back == ""
    written = [p.name for p in (tmp_path / "work" / "media" / "audio").iterdir()]
    assert written == ["ja_001_front.mp3"]


@pytest.mark.asyncio
async def test_back_run_does_not_touch_front_status(store: CardStore) -> None:
    _, rows = await run_side(store, [card()], AudioBackStage, FakeTTSClient())

    assert rows[0].audio_back_status is StageStatus.DONE
    assert rows[0].audio_front_status is StageStatus.PENDING


@pytest.mark.asyncio
async def test_back_run_preserves_a_completed_front(store: CardStore) -> None:
    """補齊另一邊時，已完成的那一邊不該被重新生成。"""
    row = card(audio_front_status=StageStatus.DONE, audio_front="media/audio/ja_001_front.mp3")

    _, rows = await run_side(store, [row], AudioBackStage, FakeTTSClient())

    assert rows[0].audio_front == "media/audio/ja_001_front.mp3"
    assert rows[0].audio_front_status is StageStatus.DONE


@pytest.mark.asyncio
async def test_one_side_failing_leaves_the_other_alone(store: CardStore) -> None:
    """front 缺文字而失敗，back 照樣完成——兩者是不同的階段。

    front 側有退路，要三個欄位都空才會真的失敗。
    """
    rows_in = [card(tts_front_text="", reading="", front="")]
    await store.write(rows_in)
    client = FakeTTSClient()

    front = await AudioFrontStage(client).run(store)
    back = await AudioBackStage(client).run(store)

    rows = await store.read()
    assert (front.failed, back.succeeded) == (1, 1)
    assert rows[0].audio_front_status is StageStatus.FAILED
    assert "tts_front_text" in rows[0].audio_front_error
    assert rows[0].audio_back_status is StageStatus.DONE
    assert rows[0].audio_back_error == ""


# ── 跳過與失敗 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_source_rows_are_skipped(store: CardStore, tmp_path: Path) -> None:
    source_row = CardRow(source="page_01.jpg", raw_text="……", ocr_source_page=1)

    result, rows = await run_side(
        store, [source_row, card()], AudioFrontStage, FakeTTSClient()
    )

    assert result.succeeded == 2
    assert rows[0].audio_front == ""
    assert rows[1].audio_front == "media/audio/ja_001_front.mp3"


@pytest.mark.asyncio
async def test_missing_text_is_recorded_as_failed(store: CardStore) -> None:
    result, rows = await run_side(
        store, [card(tts_back_text="   ")], AudioBackStage, FakeTTSClient()
    )

    assert result.failed == 1
    assert rows[0].audio_back_status is StageStatus.FAILED
    assert "tts_back_text" in rows[0].audio_back_error
    assert rows[0].audio_back == ""


@pytest.mark.asyncio
async def test_synthesis_failure_does_not_stop_the_batch(store: CardStore) -> None:
    calls: list[str] = []

    class FlakyClient(FakeTTSClient):
        async def synthesize(self, text: str) -> bytes:
            calls.append(text)
            if text == "壞的":
                raise ExternalServiceError("VOXCPM2 生成失敗（RuntimeError: CUDA OOM）")
            return WAV

    rows_in = [card("a"), card("b", tts_front_text="壞的"), card("c")]
    result, rows = await run_side(store, rows_in, AudioFrontStage, FlakyClient())

    assert (result.succeeded, result.failed) == (2, 1)
    assert len(calls) == 3
    assert rows[1].audio_front_status is StageStatus.FAILED
    assert "CUDA OOM" in rows[1].audio_front_error
    assert [rows[0].audio_front_status, rows[2].audio_front_status] == [StageStatus.DONE] * 2


# ── 選列 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_failed_selects_failed_rows(store: CardStore) -> None:
    rows_in = [
        card("a", audio_front_status=StageStatus.DONE),
        card("b", audio_front_status=StageStatus.FAILED, audio_front_error="上次失敗"),
        card("c"),
    ]
    await store.write(rows_in)
    client = FakeTTSClient()

    result = await AudioFrontStage(client).run(store, only_failed=True)

    assert result.processed == 1
    assert (await store.read())[1].audio_front_status is StageStatus.DONE


# ── 設定 ─────────────────────────────────────────────────────────


def test_concurrency_defaults_to_one_without_settings() -> None:
    assert AudioFrontStage(FakeTTSClient()).concurrency == 1


def test_concurrency_follows_tts_setting(tmp_path: Path) -> None:
    from anki_deck_builder.config import Settings, TTSSettings

    settings = Settings.model_construct(tts=TTSSettings.model_construct(concurrency=3))

    assert AudioFrontStage(FakeTTSClient(), settings=settings).concurrency == 3


def test_checkpoints_every_row() -> None:
    assert AudioFrontStage(FakeTTSClient()).checkpoint_every == 1


# ── 進度顯示 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_progress_labels_distinguish_the_two_sides(store: CardStore) -> None:
    front_stream, back_stream = io.StringIO(), io.StringIO()
    await store.write([card()])

    await AudioFrontStage(FakeTTSClient(), progress_stream=front_stream).run(store)
    await AudioBackStage(FakeTTSClient(), progress_stream=back_stream).run(store)

    assert "生成語音（單字）" in front_stream.getvalue()
    assert "生成語音（例句）" in back_stream.getvalue()


@pytest.mark.asyncio
async def test_progress_advances_on_failure(store: CardStore) -> None:
    stream = io.StringIO()
    rows = [card("a", tts_front_text=""), card("b")]

    await run_side(store, rows, AudioFrontStage, FakeTTSClient(), progress_stream=stream)

    assert stream.getvalue().splitlines()[-1].startswith("生成語音（單字）  2/2  ")


# ── 判錯語言時改送讀音（Task 9.5）────────────────────────────────

# `speech_text` 是純函式，兩張 script 表的邊界全部在這裡驗；
# 下方三條 async 測試只驗「有沒有接上、接在哪一側」。


@pytest.mark.parametrize(
    ("text", "reading", "expected"),
    [
        # (a) 成立 ＋ (b) 成立 → 換讀音
        ("足跡", "あしあと", "あしあと"),
        ("圧縮", "アッシュク", "アッシュク"),
        # `々` 是 IDEOGRAPHIC ITERATION MARK 不是漢字，漏了這兩張會掉出規則
        ("云々", "うんぬん", "うんぬん"),
        ("各々", "おのおの", "おのおの"),
        # 多個讀音取第一個
        ("後", "あと/うしろ/こう", "あと"),
        ("後", "あと／うしろ", "あと"),
        # (a) 不成立：已經有假名線索，引擎判得出來，不必動
        ("勉強する", "べんきょうする", "勉強する"),
        ("ネコ", "ねこ", "ネコ"),
        ("ability", "əˈbɪlɪti", "ability"),
        ("", "あしあと", ""),
        # (b) 不成立：白名單以外的 script 一律不動
        ("以来", "irai", "以来"),  # 羅馬字 → 引擎唸成英文
        ("足跡", "zújì", "足跡"),  # 拼音同理
        ("足跡", "ㄗㄨˊㄐㄧˋ", "足跡"),  # 注音：原文本來就唸對，換了反而壞
        ("圖書館", "도서관", "圖書館"),  # 諺文：換了連詞本身都丟了
        ("温室", "", "温室"),
        ("温室", "   ", "温室"),
        # 空白不是分隔符：OCR 標音碎片黏在一起，切了不會變對
        ("旺盛", "しよくよく おうせい", "しよくよく おうせい"),
        # 首段為空 → 退回原文，不送空字串
        ("温室", "／おんしつ", "温室"),
    ],
)
def test_speech_text(text: str, reading: str, expected: str) -> None:
    assert speech_text(text, reading) == expected


@pytest.mark.asyncio
async def test_front_speaks_the_reading_for_all_han_text(store: CardStore) -> None:
    """全漢字會被 VOXCPM2 唸成中文，改送假名。實測 342 張有 78 張落在這條。"""
    client = FakeTTSClient()

    await run_side(
        store,
        [card(tts_front_text="足跡", reading="あしあと")],
        AudioFrontStage,
        client,
    )

    assert client.calls == ["あしあと"]


@pytest.mark.asyncio
async def test_front_keeps_text_when_the_reading_cannot_rescue_it(
    store: CardStore,
) -> None:
    """羅馬字讀音唸出來是英文，比被當成中文更糟——寧可不動。"""
    client = FakeTTSClient()

    await run_side(
        store, [card(tts_front_text="以来", reading="irai")], AudioFrontStage, client
    )

    assert client.calls == ["以来"]


@pytest.mark.asyncio
async def test_back_never_swaps_in_the_reading(store: CardStore) -> None:
    """`reading` 是**條目**的讀音，換掉例句是抽換內容不是修發音。

    實測 8 筆全漢字的 `tts_back_text` 若套用此規則，`温室効果` 會被唸成 `おんしつ`。
    """
    client = FakeTTSClient()

    await run_side(
        store,
        [card(tts_back_text="温室効果", reading="おんしつ")],
        AudioBackStage,
        client,
    )

    assert client.calls == ["温室効果"]


# ── tts_front_text 的退路 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_front_falls_back_to_reading(store: CardStore) -> None:
    """有讀音就唸讀音——`prompts/extract_cards.md` 對這個欄位的規範。"""
    client = FakeTTSClient()

    await run_side(
        store, [card(tts_front_text="", reading="ぞくする")], AudioFrontStage, client
    )

    assert client.calls == ["ぞくする"]


@pytest.mark.asyncio
async def test_front_falls_back_to_front_field(store: CardStore) -> None:
    """沒有讀音概念的領域唸詞條本身。實測 308 張卡有 116 張落在這一條。"""
    client = FakeTTSClient()

    await run_side(
        store,
        [card("en_001", tts_front_text="", reading="", front="baby")],
        AudioFrontStage,
        client,
    )

    assert client.calls == ["baby"]


@pytest.mark.asyncio
async def test_front_prefers_the_explicit_field(store: CardStore) -> None:
    """退路只在欄位為空時啟用，有值時不搶。"""
    client = FakeTTSClient()

    await run_side(
        store,
        [card(tts_front_text="ぞくする", reading="別的", front="又別的")],
        AudioFrontStage,
        client,
    )

    assert client.calls == ["ぞくする"]


@pytest.mark.asyncio
async def test_front_error_lists_every_source(store: CardStore) -> None:
    _, rows = await run_side(
        store,
        [card(tts_front_text="", reading="", front="")],
        AudioFrontStage,
        FakeTTSClient(),
    )

    error = rows[0].audio_front_error
    assert "tts_front_text" in error and "reading" in error and "front" in error


@pytest.mark.asyncio
async def test_back_has_no_fallback(store: CardStore) -> None:
    """`back` 是釋義不是例句，拿來唸會變成另一件事。"""
    client = FakeTTSClient()

    result, rows = await run_side(
        store, [card(tts_back_text="", back="屬於，歸於")], AudioBackStage, client
    )

    assert result.failed == 1
    assert client.calls == []
    assert "tts_back_text" in rows[0].audio_back_error
    assert "屬於" not in rows[0].audio_back_error


# ── 要不要唸譯文 ─────────────────────────────────────────────────


def _settings(speak_translation: bool):
    from anki_deck_builder.config import Settings, TTSSettings

    return Settings.model_construct(
        tts=TTSSettings.model_construct(
            concurrency=1, speak_translation=speak_translation
        )
    )


@pytest.mark.asyncio
async def test_back_reads_only_the_sentence_by_default(store: CardStore) -> None:
    client = FakeTTSClient()
    row = card(tts_back_text="She is an actress.", example="She is an actress.\n她是女演員。")

    await run_side(store, [row], AudioBackStage, client, settings=_settings(False))

    assert client.calls == ["She is an actress."]


@pytest.mark.asyncio
async def test_back_reads_the_example_when_translation_is_wanted(store: CardStore) -> None:
    """開啟時換讀 example，不是去切 tts_back_text 的字串。"""
    client = FakeTTSClient()
    row = card(tts_back_text="She is an actress.", example="She is an actress.\n她是女演員。")

    await run_side(store, [row], AudioBackStage, client, settings=_settings(True))

    assert client.calls == ["She is an actress.\n她是女演員。"]


@pytest.mark.asyncio
async def test_back_falls_back_when_example_is_empty(store: CardStore) -> None:
    """開了設定但這張卡沒有 example，仍該唸得出原文。"""
    client = FakeTTSClient()
    row = card(tts_back_text="She is an actress.", example="")

    await run_side(store, [row], AudioBackStage, client, settings=_settings(True))

    assert client.calls == ["She is an actress."]


@pytest.mark.asyncio
async def test_front_is_unaffected_by_the_translation_setting(store: CardStore) -> None:
    client = FakeTTSClient()

    await run_side(store, [card()], AudioFrontStage, client, settings=_settings(True))

    assert client.calls == ["属する"]
