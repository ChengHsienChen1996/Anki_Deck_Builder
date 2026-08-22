"""命令列介面（介面層）。

只做參數解析、dispatch 與結果顯示。**業務邏輯一律在 `stages/`**——CLI 與 Web UI
是平行介面，兩者呼叫同一組函式，邏輯散進介面層會讓兩邊行為不一致（約束 4）。

八個子命令於 Phase 1 就全部定義完成，後續 phase 只接上實作、**不改參數結構**。
目前可用：`ocr`、`extract`、`pack`、`run-all`、`status`；`image`、`audio`、`serve`
印出提示後正常結束。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import Settings, load_settings
from .exceptions import AnkiBuilderError, StageProcessingError
from .schemas import StageStatus
from .state import STAGE_NAMES, CardStore, failed_rows, summarize

#: 尚未實作的子命令 → 提示訊息
NOT_IMPLEMENTED: dict[str, str] = {
    "image": "image 於 Phase 3 實作（ComfyUI 聯想圖生成）。",
    "audio": "audio 於 Phase 4 實作（VOXCPM2 語音生成）。",
    "serve": "serve 於 Phase 5 實作（本地 Web UI）。",
}


def _extract_agent_name(settings: Settings) -> str:
    """抽取階段實際會用到的 agent（兩條輸入路徑用不同 agent，但模型相同）。"""
    from .stages.extract import TEXT_AGENT, VISION_AGENT

    return VISION_AGENT if settings.ingest.mode == "vision_direct" else TEXT_AGENT


#: 會實際呼叫模型的子命令
AGENT_COMMANDS: frozenset[str] = frozenset({"ocr", "extract", "run-all"})


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

    if args.command in AGENT_COMMANDS:
        _disable_agent_tracing()

    if args.command == "status":
        return await _run_status(store)
    if args.command == "ocr":
        return await _run_ocr(args, settings, store)
    if args.command == "extract":
        return await _run_extract(args, settings, store)
    if args.command == "pack":
        return await _run_pack(args, settings, store)
    if args.command == "run-all":
        return await _run_all(args, settings, store)

    raise AssertionError(f"未處理的子命令：{args.command}")  # pragma: no cover


# ── 子命令 ───────────────────────────────────────────────────────


def _disable_agent_tracing() -> None:
    """關掉 openai-agents 的 trace 上傳。

    它預設會把每次執行的 trace 送往 OpenAI，而本專案打的是本地 Ollama、
    `OPENAI_API_KEY` 是佔位字串，於是每次執行都會噴
    `[non-fatal] Tracing client error 401`。submodule 的參考實作
    （`tests/test_multimodal.py`）同樣明確關閉。
    """
    from agents import set_tracing_disabled

    set_tracing_disabled(True)


async def _free_vram_for(settings: Settings, endpoint: tuple[str, str]) -> None:
    """階段開始前卸載其他常駐模型。

    本專案的兩個模型在 24 GB 卡上無法共存（抽取 20.3 GB + OCR 2.2 GB + 桌面）。
    Ollama 預設 `keep_alive` 5 分鐘不會主動讓位，前一階段的模型還在時，
    這一階段的請求會卡在排隊、`still_waiting` 一路累積到逾時。
    """
    if not settings.model_unload.before_stage:
        return

    from .clients.model_unload import ensure_room

    base_url, model = endpoint
    freed = await ensure_room(base_url, model, wait_timeout=settings.model_unload.timeout)
    if freed:
        print(f"（已卸載 {'、'.join(freed)} 以騰出 VRAM）")


def _build_ocr_stage(settings: Settings):  # noqa: ANN201 - 回傳型別需延後匯入
    """組出 OCR 階段，並依設定決定要不要接上 VRAM 讓渡。

    卸載是 Ollama 專屬手段，`agents.yaml` 可指向任何供應商，因此判斷放在這裡
    （介面層的接線），而不是寫死進階段或 client（architecture.md 的要求）。
    """
    from .clients.model_unload import unload_model
    from .clients.ocr_client import OCRClient
    from .stages.ocr import OCRStage

    client = OCRClient(settings.agent_factory.yaml_settings_file)
    on_finish = None
    if settings.model_unload.enabled:
        base_url, model = client.model_endpoint()

        async def on_finish() -> bool:  # noqa: F811 - 只在啟用時定義
            return await unload_model(
                base_url, model, wait_timeout=settings.model_unload.timeout
            )

    return OCRStage(client, settings=settings, on_finish=on_finish)


async def _run_ocr(
    args: argparse.Namespace, settings: Settings, store: CardStore
) -> int:
    stage = _build_ocr_stage(settings)

    if args.input:
        prepared = await stage.prepare(store, args.input)
        message = f"ocr：{prepared.kind} 輸入，新增 {prepared.created} 列"
        if prepared.skipped:
            message += f"（略過 {prepared.skipped} 個已在工作檔中的來源）"
        print(message)
    elif not store.exists():
        raise AnkiBuilderError(
            f"工作檔不存在且未指定 --input：{store.path}。"
            "第一次執行請用 anki-builder ocr --input <路徑>。"
        )

    if not stage.is_vision_direct:
        await _free_vram_for(settings, stage.client.model_endpoint())

    result = await stage.run(store, force=args.force, only_failed=args.only_failed)

    if stage.is_vision_direct:
        print("ocr：INGEST_MODE=vision_direct，僅建立列，未呼叫 OCR。")
        return 0

    print(
        f"ocr：處理 {result.processed} 列"
        f"（成功 {result.succeeded}、失敗 {result.failed}）"
    )
    if result.failed:
        print("有失敗的列，執行 anki-builder status 看明細。", file=sys.stderr)
        return 1
    return 0


async def _run_extract(
    args: argparse.Namespace, settings: Settings, store: CardStore
) -> int:
    # 延後匯入：agent_factory 相依較重，--help 與 status 不該為它付出啟動成本
    from .clients.llm_client import LLMClient
    from .stages.extract import ExtractStage

    client = LLMClient(settings.agent_factory.yaml_settings_file)
    stage = ExtractStage(
        client,
        settings=settings,
        deck_name=args.deck_name,
        domain=args.domain,
        source=args.source,
        card_id_prefix=args.card_id_prefix,
    )
    await _free_vram_for(settings, client.model_endpoint(_extract_agent_name(settings)))
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


async def _run_all(
    args: argparse.Namespace, settings: Settings, store: CardStore
) -> int:
    """依序執行 ocr → extract → pack。

    **任一階段有 failed 的列都不中斷**——失敗已記錄在該列上，後面的階段照樣
    處理其餘的列，最後由 `pack` 一次擋下。這樣一趟跑完能看到全部問題，
    而不是修一個、重跑一次、再冒出下一個。

    `image` 與 `audio` 的呼叫位置見下方註解，Phase 3／4 接上。
    """
    from .stages.pack import pack

    exit_code = 0

    # ① ocr
    ocr_stage = _build_ocr_stage(settings)
    if args.input:
        prepared = await ocr_stage.prepare(store, args.input)
        print(f"① ocr：{prepared.kind} 輸入，新增 {prepared.created} 列")
    elif not store.exists():
        raise AnkiBuilderError(
            f"工作檔不存在且未指定 --input：{store.path}。"
            "run-all 第一次執行請用 --input <路徑>。"
        )
    if not ocr_stage.is_vision_direct:
        await _free_vram_for(settings, ocr_stage.client.model_endpoint())
    ocr_result = await ocr_stage.run(store, force=args.force, only_failed=args.only_failed)
    if ocr_stage.is_vision_direct:
        print("① ocr：vision_direct，僅建立列，未呼叫 OCR")
    else:
        print(f"① ocr：成功 {ocr_result.succeeded}、失敗 {ocr_result.failed}")
        exit_code |= 1 if ocr_result.failed else 0

    # ② extract
    from .clients.llm_client import LLMClient
    from .stages.extract import ExtractStage

    llm_client = LLMClient(settings.agent_factory.yaml_settings_file)
    extract_stage = ExtractStage(
        llm_client,
        settings=settings,
        deck_name=args.deck_name if hasattr(args, "deck_name") else None,
    )
    await _free_vram_for(settings, llm_client.model_endpoint(_extract_agent_name(settings)))
    extract_result = await extract_stage.run(
        store, force=args.force, only_failed=args.only_failed
    )
    print(
        f"② extract：成功 {extract_result.succeeded}、失敗 {extract_result.failed}，"
        f"新增 {extract_result.added} 張卡"
    )
    exit_code |= 1 if extract_result.failed else 0

    # ③ image —— Phase 3 在此接上 ImageStage，簽章同 extract
    # ④ audio —— Phase 4 在此接上 AudioStage（--side front|back|both）

    # ⑤ pack
    output = Path(args.output) if args.output else settings.paths.output_dir / "deck.zip"
    try:
        pack_result = await pack(store, output)
    except StageProcessingError as exc:
        # 前面的階段刻意不中斷，問題累積到這裡一次擋下
        print(f"⑤ pack 中止：{exc}", file=sys.stderr)
        print(
            "請先 anki-builder status 看明細，修正後以個別子命令重跑失敗的階段"
            "（例：anki-builder ocr --only-failed）；"
            "或 anki-builder pack --allow-failed 略過這些列。",
            file=sys.stderr,
        )
        return 1
    print(f"⑤ pack：{pack_result.card_count} 張卡 → {pack_result.output}")

    if exit_code:
        print("部分列失敗，執行 anki-builder status 看明細。", file=sys.stderr)
    return exit_code


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
