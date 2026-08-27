"""進度顯示單元測試。

時間以假時鐘控制，輸出寫進 `StringIO`——不依賴真實終端機，也不會因為機器快慢而飄。
"""

from __future__ import annotations

import io

import pytest

from anki_deck_builder.stages.progress import (
    BAR_WIDTH,
    ProgressReporter,
    format_duration,
)


class Clock:
    """可手動推進的假時鐘。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TTYStream(io.StringIO):
    """假裝自己是終端機的 StringIO。"""

    def isatty(self) -> bool:
        return True


def make(total: int, tty: bool = True, **kwargs) -> tuple[ProgressReporter, io.StringIO, Clock]:
    stream = TTYStream() if tty else io.StringIO()
    clock = Clock()
    reporter = ProgressReporter("生成聯想圖", total, stream=stream, clock=clock, **kwargs)
    return reporter, stream, clock


# ── 時間格式 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00"),
        (9, "00:09"),
        (495, "08:15"),
        (59.9, "00:59"),
        (3600, "1:00:00"),
        (3661, "1:01:01"),
        (-5, "00:00"),
    ],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


# ── 百分比與進度條 ───────────────────────────────────────────────


def test_bar_is_empty_at_start() -> None:
    reporter, _, _ = make(100)

    assert f"[{'░' * BAR_WIDTH}]" in reporter.render()


def test_bar_fills_proportionally() -> None:
    reporter, _, _ = make(100)
    reporter.advance(50)

    assert f"[{'█' * (BAR_WIDTH // 2)}{'░' * (BAR_WIDTH // 2)}]" in reporter.render()


def test_bar_is_full_at_completion() -> None:
    reporter, _, _ = make(4)
    reporter.advance(4)

    assert f"[{'█' * BAR_WIDTH}]" in reporter.render()


def test_counts_never_exceed_total() -> None:
    """骨架多叫一次也不該顯示 101/100。"""
    reporter, _, _ = make(3)
    reporter.advance(5)

    assert reporter.done == 3
    assert "3/3" in reporter.render()


# ── 時間預估 ─────────────────────────────────────────────────────


def test_render_matches_the_documented_format() -> None:
    """整行格式照 phase-3-image.md 的規格。

    數字取整除得盡的組合：40 項花 480 秒 → 每項 12 秒，剩 60 項即 720 秒。
    """
    reporter, _, clock = make(100)
    reporter.advance(40)
    clock.advance(480)

    assert reporter.render() == (
        "生成聯想圖  [██████░░░░░░░░░░]  40/100  已耗時 08:00  預估剩餘 12:00"
    )


def test_remaining_is_unknown_before_first_item() -> None:
    """一項都還沒完成時無從推估，不該顯示 00:00 誤導使用者。"""
    reporter, _, clock = make(10)
    clock.advance(30)

    assert "預估剩餘 --:--" in reporter.render()


def test_remaining_is_zero_at_completion() -> None:
    reporter, _, clock = make(2)
    clock.advance(10)
    reporter.advance(2)

    assert "預估剩餘 00:00" in reporter.render()


def test_remaining_uses_average_rate() -> None:
    """每項 20 秒、剩 3 項 → 01:00。"""
    reporter, _, clock = make(5)
    clock.advance(40)
    reporter.advance(2)

    assert "已耗時 00:40" in reporter.render()
    assert "預估剩餘 01:00" in reporter.render()


# ── 終端機輸出 ───────────────────────────────────────────────────


def test_tty_rewrites_one_line() -> None:
    reporter, stream, _ = make(3)
    reporter.start()
    reporter.advance()
    reporter.advance()

    output = stream.getvalue()
    assert output.count("\r") == 3
    assert "\n" not in output


def test_tty_finish_appends_newline() -> None:
    reporter, stream, _ = make(2)
    reporter.advance(2)
    reporter.finish()

    assert stream.getvalue().endswith("\n")
    assert "2/2" in stream.getvalue()


# ── 非 TTY 降級 ──────────────────────────────────────────────────


def test_plain_mode_prints_every_n_items() -> None:
    reporter, stream, _ = make(25, tty=False)
    for _ in range(25):
        reporter.advance()
    reporter.finish()

    lines = stream.getvalue().splitlines()
    assert [line.split("  ")[1] for line in lines] == ["10/25", "20/25", "25/25"]


def test_plain_mode_omits_control_characters() -> None:
    """`\\r` 在 log 檔裡會疊成亂碼，降級輸出不該有它，也不該有進度條。"""
    reporter, stream, _ = make(10, tty=False)
    for _ in range(10):
        reporter.advance()

    output = stream.getvalue()
    assert "\r" not in output
    assert "█" not in output and "░" not in output
    assert output == "生成聯想圖  10/10  已耗時 00:00  預估剩餘 00:00\n"


def test_plain_mode_always_reports_the_last_item() -> None:
    """總數不是 plain_every 的倍數時，最後一項仍要留下紀錄。"""
    reporter, stream, _ = make(7, tty=False)
    for _ in range(7):
        reporter.advance()

    assert stream.getvalue().splitlines() == [
        "生成聯想圖  7/7  已耗時 00:00  預估剩餘 00:00"
    ]


def test_plain_every_is_configurable() -> None:
    reporter, stream, _ = make(6, tty=False, plain_every=2)
    for _ in range(6):
        reporter.advance()

    assert len(stream.getvalue().splitlines()) == 3


def test_plain_mode_prints_nothing_on_start_or_finish() -> None:
    """log 不需要「開始了」與「結束了」兩行空進度。"""
    reporter, stream, _ = make(5, tty=False)
    reporter.start()
    reporter.finish()

    assert stream.getvalue() == ""


# ── 邊界 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("tty", [True, False])
def test_zero_total_prints_nothing(tty: bool) -> None:
    """沒有待處理列時整個階段是 no-op，不該冒出一條 0/0 的進度條。"""
    reporter, stream, _ = make(0, tty=tty)
    reporter.start()
    reporter.advance()
    reporter.finish()

    assert stream.getvalue() == ""


def test_non_tty_stream_without_isatty() -> None:
    """取不到 isatty 的串流一律當作非終端機。"""

    class Bare:
        def __init__(self) -> None:
            self.written: list[str] = []

        def write(self, text: str) -> int:
            self.written.append(text)
            return len(text)

    stream = Bare()
    reporter = ProgressReporter("生成聯想圖", 1, stream=stream)  # type: ignore[arg-type]
    reporter.advance()

    assert stream.written == ["生成聯想圖  1/1  已耗時 00:00  預估剩餘 00:00\n"]
