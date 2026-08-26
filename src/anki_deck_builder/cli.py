"""命令列介面（介面層）。

只做參數解析、dispatch 與結果顯示。**業務邏輯一律在 `stages/`**——CLI 與 Web UI
是平行介面，兩者呼叫同一組函式，邏輯散進介面層會讓兩邊行為不一致（約束 4）。

八個子命令於 Phase 1 就全部定義完成，後續 phase 只接上實作、**不改參數結構**。
八個全部可用：`ocr`、`extract`、`image`、`audio`、`pack`、`run-all`、`status`、`serve`。
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
from .stages.factory import (
    build_audio_stages,
    build_extract_stage,
    build_image_stage,
    build_ocr_stage,
)
from .stages.vram import (
    extract_agent_name,
    free_vram_for,
    free_vram_for_local_gpu,
    release_comfyui,
)
from .state import STAGE_NAMES, CardStore, failed_rows, summarize

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
    # 預設自動判斷；兩個旗標是判斷失準時的覆寫手段，互斥
    enrich_group = extract.add_mutually_exclusive_group()
    enrich_group.add_argument(
        "--enrich",
        dest="enrich",
        action="store_true",
        default=None,
        help="強制先補釋義再抽取（索引頁、單字表）。預設由模型自動判斷",
    )
    enrich_group.add_argument(
        "--no-enrich",
        dest="enrich",
        action="store_false",
        help="強制不補釋義，直接抽取",
    )
    extract.add_argument(
        "--deck-categories",
        metavar="LIST",
        help="deck 最後一層的可選分類，以 ／ 或 , 分隔，例如 名詞／動詞／形容詞",
    )
    extract.add_argument(
        "--card-language",
        metavar="LANG",
        help="釋義（back）的書寫語言，例如 繁體中文。原文書或純單字表需要它",
    )
    extract.add_argument(
        "--card-id-prefix", metavar="TEXT", help="card_id 前綴（會再串上頁碼）"
    )

    subparsers.add_parser("image", parents=[selection], help="③ 聯想圖生成")
    audio = subparsers.add_parser("audio", parents=[selection], help="④ 語音生成")
    audio.add_argument(
        "--side",
        choices=("front", "back", "both"),
        default="both",
        help="要生成哪一側的語音（預設 both）。front／back 完全不觸碰另一側的狀態",
    )

    pack = subparsers.add_parser("pack", parents=[work], help="⑤ 路徑整合與打包")
    pack.add_argument("--output", metavar="PATH", help="輸出 ZIP 路徑（預設 $OUTPUT_DIR/deck.zip）")
    pack.add_argument(
        "--allow-failed", action="store_true", help="即使有 failed 的列也照樣打包"
    )

    run_all = subparsers.add_parser("run-all", parents=[selection], help="①–⑤ 全流程")
    run_all.add_argument("--input", metavar="PATH", help="影像或 PDF 的檔案／目錄路徑")
    run_all.add_argument("--output", metavar="PATH", help="輸出 ZIP 路徑")
    run_all.add_argument("--deck-name", metavar="NAME", help="牌組名稱前綴，例如 日語::N2")
    run_all.add_argument("--card-language", metavar="LANG", help="釋義的書寫語言，例如 繁體中文")
    run_all.add_argument("--deck-categories", metavar="LIST", help="deck 最後一層的可選分類")
    run_all_enrich = run_all.add_mutually_exclusive_group()
    run_all_enrich.add_argument(
        "--enrich", dest="enrich", action="store_true", default=None, help="強制先補釋義再抽取"
    )
    run_all_enrich.add_argument(
        "--no-enrich", dest="enrich", action="store_false", help="強制不補釋義"
    )

    subparsers.add_parser("status", parents=[work], help="各階段狀態統計")

    reset = subparsers.add_parser(
        "reset", parents=[work], help="重置階段狀態，或清空工作檔"
    )
    what = reset.add_mutually_exclusive_group(required=True)
    what.add_argument(
        "--stages",
        metavar="LIST",
        help=f"把這些階段設回 pending（逗號分隔）。合法值：{'、'.join(STAGE_NAMES)}",
    )
    what.add_argument(
        "--clear",
        choices=("rows", "all"),
        help="清空所有列；all 連 media/ 底下的圖與語音一起刪",
    )
    reset.add_argument(
        "--yes", action="store_true", help="確認執行 --clear（不給則只顯示會刪掉什麼）"
    )
    reset.add_argument(
        "--no-backup", action="store_true", help="不要先備份 cards.csv（不建議）"
    )

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
    settings = load_settings()
    # serve 沒有 --work（Phase 1 定案的參數結構不改），走設定的預設工作檔
    store = CardStore(getattr(args, "work", None) or settings.paths.cards_csv)

    if args.command in AGENT_COMMANDS:
        _disable_agent_tracing()

    if args.command == "status":
        return await _run_status(store)
    if args.command == "ocr":
        return await _run_ocr(args, settings, store)
    if args.command == "extract":
        return await _run_extract(args, settings, store)
    if args.command == "image":
        return await _run_image(args, settings, store)
    if args.command == "audio":
        return await _run_audio(args, settings, store)
    if args.command == "pack":
        return await _run_pack(args, settings, store)
    if args.command == "run-all":
        return await _run_all(args, settings, store)
    if args.command == "reset":
        return await _run_reset(args, store)
    if args.command == "serve":
        return await _run_serve(args, store)

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


async def _free_vram_for_local_gpu(settings: Settings) -> None:
    """`stages.vram` 的 CLI 版薄殼：卸載結果寫 stdout、略過訊息寫 stderr。"""
    await free_vram_for_local_gpu(
        settings, notify=print, on_skip=lambda message: print(message, file=sys.stderr)
    )


async def _run_ocr(
    args: argparse.Namespace, settings: Settings, store: CardStore
) -> int:
    stage = build_ocr_stage(settings)

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
        await free_vram_for(settings, stage.client.model_endpoint(), notify=print)

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
    stage = build_extract_stage(
        settings,
        deck_name=args.deck_name,
        domain=args.domain,
        source=args.source,
        card_id_prefix=args.card_id_prefix,
        card_language=args.card_language,
        deck_categories=args.deck_categories,
        enrich=args.enrich,
    )
    await release_comfyui(settings, notify=print)
    await free_vram_for(
        settings, stage.client.model_endpoint(extract_agent_name(settings)), notify=print
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


async def _run_image(
    args: argparse.Namespace, settings: Settings, store: CardStore
) -> int:
    stage = build_image_stage(settings)
    await _free_vram_for_local_gpu(settings)
    result = await stage.run(store, force=args.force, only_failed=args.only_failed)

    print(
        f"image：處理 {result.processed} 列"
        f"（成功 {result.succeeded}、失敗 {result.failed}）"
    )
    if result.failed:
        print("有失敗的列，執行 anki-builder status 看明細。", file=sys.stderr)
        return 1
    return 0


async def _run_audio(
    args: argparse.Namespace, settings: Settings, store: CardStore
) -> int:
    stages = build_audio_stages(settings, args.side)
    await _free_vram_for_local_gpu(settings)

    exit_code = 0
    for stage in stages:
        result = await stage.run(store, force=args.force, only_failed=args.only_failed)
        print(
            f"{stage.name}：處理 {result.processed} 列"
            f"（成功 {result.succeeded}、失敗 {result.failed}）"
        )
        exit_code |= 1 if result.failed else 0

    if exit_code:
        print("有失敗的列，執行 anki-builder status 看明細。", file=sys.stderr)
    return exit_code


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
    """依序執行 ocr → extract → image → audio → pack。

    **任一階段有 failed 的列都不中斷**——失敗已記錄在該列上，後面的階段照樣
    處理其餘的列，最後由 `pack` 一次擋下。這樣一趟跑完能看到全部問題，
    而不是修一個、重跑一次、再冒出下一個。

    """
    from .stages.pack import pack

    exit_code = 0

    # ① ocr
    ocr_stage = build_ocr_stage(settings)
    if args.input:
        prepared = await ocr_stage.prepare(store, args.input)
        print(f"① ocr：{prepared.kind} 輸入，新增 {prepared.created} 列")
    elif not store.exists():
        raise AnkiBuilderError(
            f"工作檔不存在且未指定 --input：{store.path}。"
            "run-all 第一次執行請用 --input <路徑>。"
        )
    if not ocr_stage.is_vision_direct:
        await free_vram_for(settings, ocr_stage.client.model_endpoint(), notify=print)
    ocr_result = await ocr_stage.run(store, force=args.force, only_failed=args.only_failed)
    if ocr_stage.is_vision_direct:
        print("① ocr：vision_direct，僅建立列，未呼叫 OCR")
    else:
        print(f"① ocr：成功 {ocr_result.succeeded}、失敗 {ocr_result.failed}")
        exit_code |= 1 if ocr_result.failed else 0

    # ② extract
    extract_stage = build_extract_stage(
        settings,
        deck_name=getattr(args, "deck_name", None),
        card_language=getattr(args, "card_language", None),
        deck_categories=getattr(args, "deck_categories", None),
        enrich=getattr(args, "enrich", None),
    )
    await release_comfyui(settings, notify=print)
    await free_vram_for(
        settings,
        extract_stage.client.model_endpoint(extract_agent_name(settings)),
        notify=print,
    )
    extract_result = await extract_stage.run(
        store, force=args.force, only_failed=args.only_failed
    )
    print(
        f"② extract：成功 {extract_result.succeeded}、失敗 {extract_result.failed}，"
        f"新增 {extract_result.added} 張卡"
    )
    exit_code |= 1 if extract_result.failed else 0

    # ③ image
    image_stage = build_image_stage(settings)
    await _free_vram_for_local_gpu(settings)
    image_result = await image_stage.run(
        store, force=args.force, only_failed=args.only_failed
    )
    print(f"③ image：成功 {image_result.succeeded}、失敗 {image_result.failed}")
    exit_code |= 1 if image_result.failed else 0

    # ④ audio —— 必須排在 image 之後且不併行：兩者都吃 GPU
    audio_stages = build_audio_stages(settings, getattr(args, "side", "both"))
    await _free_vram_for_local_gpu(settings)
    for stage in audio_stages:
        audio_result = await stage.run(
            store, force=args.force, only_failed=args.only_failed
        )
        print(
            f"④ {stage.name}：成功 {audio_result.succeeded}、失敗 {audio_result.failed}"
        )
        exit_code |= 1 if audio_result.failed else 0

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


async def _run_serve(args: argparse.Namespace, store: CardStore) -> int:
    """啟動本地 Web UI，直到 Ctrl-C。

    服務跑在 `main()` 已經開好的那個事件迴圈上（理由見 `web/server.serve()`）。
    """
    from .web.server import serve

    print(f"Web UI：http://{args.host}:{args.port}（Ctrl-C 結束）")
    await serve(host=args.host, port=args.port, work=store.path)
    return 0


async def _run_reset(args: argparse.Namespace, store: CardStore) -> int:
    """重置階段狀態或清空工作檔。

    `--clear` 是破壞性的，因此**預設什麼都不做**，只印出會刪掉多少東西；
    真的要執行得再加 `--yes`。這條規則與 Web UI 的「輸入工作檔名稱才啟用」
    是同一件事的兩種介面呈現。
    """
    from .state import clear_rows, count_media, reset_stages

    backup = not args.no_backup

    if args.stages:
        names = [name.strip() for name in args.stages.split(",") if name.strip()]
        try:
            result = await reset_stages(store, names, backup=backup)
        except ValueError as error:
            raise AnkiBuilderError(str(error)) from error
        print(
            f"reset：{'、'.join(result.stages)} 已設回 pending"
            f"（改動 {result.affected_rows} 列）"
        )
        _report_backup(result.backup)
        return 0

    media_dirs = _media_dirs(store) if args.clear == "all" else []
    rows = await store.read() if store.exists() else []
    cards = sum(1 for row in rows if row.card_id)
    media_count = await count_media(media_dirs)

    if not args.yes:
        print(
            f"這會清掉 {len(rows)} 列（其中 {cards} 張卡）"
            + (f"與 {media_count} 個媒體檔" if media_dirs else "，媒體檔保留")
            + "。確定的話加上 --yes 再執行一次。"
        )
        return 1

    result = await clear_rows(store, media_dirs, backup=backup)
    print(
        f"reset：已清空 {result.cleared_rows} 列"
        + (f"、刪除 {result.removed_media} 個媒體檔" if media_dirs else "")
    )
    _report_backup(result.backup)
    for failure in result.failures:
        print(f"  ⚠️ 刪不掉：{failure}", file=sys.stderr)
    return 0


def _media_dirs(store: CardStore) -> list[Path]:
    """要清掉的媒體目錄。版面的權威定義在 `stages/pack.py`，不在這裡重寫。"""
    from .stages.pack import media_directories

    return media_directories(Path(store.path).parent)


def _report_backup(path: Path | None) -> None:
    print(f"（已備份 {path.name}）" if path else "（工作檔原本不存在，未備份）")


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
