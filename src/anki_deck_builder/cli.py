"""命令列介面（介面層）。

只做參數解析、dispatch 與結果顯示。**業務邏輯一律在 `stages/`**——CLI 與 Web UI
是平行介面，兩者呼叫同一組函式，邏輯散進介面層會讓兩邊行為不一致（約束 4）。

八個子命令現在就全部定義完成，Phase 1 只有 `extract`、`pack`、`status` 實際可用，
其餘印出提示後正常結束。後續 phase 只接上實作，**不改參數結構**。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import Settings, load_settings
from .exceptions import AnkiBuilderError
from .schemas import StageStatus
from .state import STAGE_NAMES, CardStore, failed_rows, summarize

#: 尚未實作的子命令 → 提示訊息
NOT_IMPLEMENTED: dict[str, str] = {
    "ocr": "ocr 於 Phase 2 實作（影像／PDF 轉文字）。",
    "image": "image 於 Phase 3 實作（ComfyUI 聯想圖生成）。",
    "audio": "audio 於 Phase 4 實作（VOXCPM2 語音生成）。",
    "serve": "serve 於 Phase 5 實作（本地 Web UI）。",
    "run-all": (
        "run-all 於 Phase 5 全部階段齊備後可用。"
        "Phase 1 請分別執行：anki-builder extract → anki-builder pack。"
    ),
}


def build_parser() -> argparse.ArgumentParser:
    """建立 argparse 解析器。"""
    parser = argparse.ArgumentParser(
        prog="anki-builder",
        description="把書本影像或純文字轉成 Anki 記憶引擎可載入的牌組 ZIP。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<子命令>")

    work = argparse.ArgumentParser(add_help=False)
    work.add_argument(
        "--work",
        metavar="PATH",
        help="中間 CSV 路徑（預設 $WORK_DIR/cards.csv）",
    )

    # --force 與 --only-failed 互斥，由 argparse 在解析階段直接擋下
    selection = argparse.ArgumentParser(add_help=False, parents=[work])
    group = selection.add_mutually_exclusive_group()
    group.add_argument("--force", action="store_true", help="忽略 done，強制全部重跑")
    group.add_argument(
        "--only-failed", action="store_true", help="只處理 failed 的列"
    )

    ocr = subparsers.add_parser("ocr", parents=[selection], help="① 影像／PDF 轉文字")
    ocr.add_argument("--input", metavar="PATH", help="影像或 PDF 的檔案／目錄路徑")

    extract = subparsers.add_parser(
        "extract", parents=[selection], help="② LLM 抽取整理"
    )
    extract.add_argument("--deck-name", metavar="NAME", help="牌組名稱前綴，例如 日語::N2")
    extract.add_argument("--domain", metavar="TEXT", help="學習領域，例如 日語 N2 單字")
    extract.add_argument("--source", metavar="TEXT", help="來源，例如 單字書 p.333")
    extract.add_argument(
        "--card-id-prefix", metavar="TEXT", help="card_id 前綴（會再串上頁碼）"
    )

    subparsers.add_parser("image", parents=[selection], help="③ 聯想圖生成")
    subparsers.add_parser("audio", parents=[selection], help="④ 語音生成")

    pack = subparsers.add_parser("pack", parents=[work], help="⑤ 路徑整合與打包")
    pack.add_argument("--output", metavar="PATH", help="輸出 ZIP 路徑（預設 $OUTPUT_DIR/deck.zip）")
    pack.add_argument(
        "--allow-failed", action="store_true", help="即使有 failed 的列也照樣打包"
    )

    run_all = subparsers.add_parser("run-all", parents=[selection], help="①–⑤ 全流程")
    run_all.add_argument("--input", metavar="PATH", help="影像或 PDF 的檔案／目錄路徑")
    run_all.add_argument("--output", metavar="PATH", help="輸出 ZIP 路徑")

    subparsers.add_parser("status", parents=[work], help="各階段狀態統計")

    serve = subparsers.add_parser("serve", help="啟動本地 Web UI")
    serve.add_argument("--host", default="127.0.0.1", help="監聽位址")
    serve.add_argument("--port", type=int, default=7860, help="監聽埠")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """進入點。回傳行程結束碼。"""
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except AnkiBuilderError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command in NOT_IMPLEMENTED:
        print(NOT_IMPLEMENTED[args.command])
        return 0

    settings = load_settings()
    store = CardStore(args.work or settings.paths.cards_csv)

    if args.command == "status":
        return await _run_status(store)
    if args.command == "extract":
        return await _run_extract(args, settings, store)
    if args.command == "pack":
        return await _run_pack(args, settings, store)

    raise AssertionError(f"未處理的子命令：{args.command}")  # pragma: no cover


# ── 子命令 ───────────────────────────────────────────────────────


async def _run_extract(
    args: argparse.Namespace, settings: Settings, store: CardStore
) -> int:
    # 延後匯入：agent_factory 相依較重，--help 與 status 不該為它付出啟動成本
    from .clients.llm_client import LLMClient
    from .stages.extract import ExtractStage

    stage = ExtractStage(
        LLMClient(settings.agent_factory.yaml_settings_file),
        settings=settings,
        deck_name=args.deck_name,
        domain=args.domain,
        source=args.source,
        card_id_prefix=args.card_id_prefix,
    )
    result = await stage.run(store, force=args.force, only_failed=args.only_failed)

    print(
        f"extract：處理 {result.processed} 列"
        f"（成功 {result.succeeded}、失敗 {result.failed}），新增 {result.added} 張卡"
    )
    if result.failed:
        print("有失敗的列，執行 anki-builder status 看明細。", file=sys.stderr)
        return 1
    return 0


async def _run_pack(
    args: argparse.Namespace, settings: Settings, store: CardStore
) -> int:
    from .stages.pack import pack

    output = Path(args.output) if args.output else settings.paths.output_dir / "deck.zip"
    result = await pack(store, output, allow_failed=args.allow_failed)

    print(
        f"pack：{result.card_count} 張卡、{result.media_count} 個媒體檔 → {result.output}"
    )
    if result.skipped_source_rows:
        print(f"（略過 {result.skipped_source_rows} 列 raw_text 來源列）")
    return 0


async def _run_status(store: CardStore) -> int:
    rows = await store.read()
    print(_format_status(store.path, rows))
    return 0


def _format_status(path: Path, rows: list) -> str:
    summary = summarize(rows)
    lines = [f"{path} — 共 {len(rows)} 列", ""]
    # 「階段」是全形字，終端寬度是 4 而非 2，因此標頭的欄寬比資料列少 2
    lines.append(f"{'階段':<12}{'pending':>9}{'done':>7}{'failed':>9}")
    lines.append("─" * 41)
    for stage in STAGE_NAMES:
        counts = summary[stage]
        lines.append(
            f"{stage:<16}"
            f"{counts[StageStatus.PENDING]:>7}"
            f"{counts[StageStatus.DONE]:>7}"
            f"{counts[StageStatus.FAILED]:>9}"
        )

    for stage in STAGE_NAMES:
        failures = failed_rows(rows, stage)
        if not failures:
            continue
        lines.append("")
        lines.append(f"失敗明細（{stage}）")
        for row in failures:
            reason = getattr(row, f"{stage}_error") or "未記錄原因"
            lines.append(f"  {row.card_id or '(來源列)'}  {reason}")

    return "\n".join(lines)
