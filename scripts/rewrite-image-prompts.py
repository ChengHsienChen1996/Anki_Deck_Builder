#!/usr/bin/env python
"""把既有卡片的 `image_prompt` 改寫成不會讓圖像模型寫出文字的版本。

`prompts/extract_cards.md` 已加上「不要用會帶出文字的道具」的規則，**新抽取的卡片
不會再有這個問題**；這支腳本是給規則上線前做好的牌組用的。

只改 `image_prompt` 一欄並把 `image_status` 設回 `pending`——重跑 `extract` 會連
`front`／`back`／`example` 全部重生，那些欄位是人工檢視過的。

用法：

    uv run python scripts/rewrite-image-prompts.py work/cards.csv --dry-run
    uv run python scripts/rewrite-image-prompts.py work/cards.csv
    uv run python scripts/rewrite-image-prompts.py work/cards.csv --all
    uv run python scripts/rewrite-image-prompts.py work/cards.csv --card-ids p1_006,p1_009

預設只處理**偵測到文字道具**的列。`--all` 則不篩選，全部送給模型重看一次
（模型被要求「本來就沒問題的原樣輸出」，但 308 次呼叫要花不少時間）。
"""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

from anki_deck_builder.clients.llm_client import LLMClient
from anki_deck_builder.config import load_settings
from anki_deck_builder.schemas import CardRow, StageStatus
from anki_deck_builder.state import CardStore

#: 會讓圖像模型自己寫字的道具。與 prompts/image_prompt_rewrite.md 的表格對應。
#: **不含 paper**——改寫規則本身就把「空白紙張」列為建議的替代品，
#: 把它算成問題會讓改寫後的結果被誤判為沒改乾淨
TEXT_PROPS: tuple[str, ...] = (
    "screen", "monitor", "display", "laptop",
    "book", "magazine", "newspaper", "notebook",
    "calendar", "clock",
    "sign", "signboard", "poster", "banner", "label", "nameplate",
    "chart", "graph", "diagram", "whiteboard", "blackboard",
    "document", "ticket", "receipt", "certificate",
    # 以下是實測補上的：模型看到「符號／卡片」這類物件會自己寫字上去
    # （p2_016 的書封出現亂碼、p2_034 的名片出現假日文）
    "card", "symbol", "logo", "badge", "plaque", "stamp",
    "envelope", "menu", "map", "invoice", "letterhead",
)

#: 統一風格後綴。改寫後必須原樣保留——權威定義在 prompts/image_prompt_template.md
STYLE_SUFFIX = (
    ", cinematic lighting, muted color palette, soft shadows, atmospheric, "
    "no text, no letters, no watermark"
)

AGENT = "ImagePromptAgent"


def has_text_prop(prompt: str) -> list[str]:
    """prompt 的場景描述裡有哪些文字道具（後綴不算）。

    連字號複合詞不算：`off-screen`（畫面外）是**改寫後**常見的正確寫法，
    用 `\bscreen\b` 比對會把它誤判成還有螢幕。
    """
    scene = prompt.split(", cinematic lighting")[0].lower()
    return [
        word
        for word in TEXT_PROPS
        if re.search(rf"(?<![-\w]){word}s?\b", scene)
    ]


def build_input(row: CardRow) -> str:
    """給模型的輸入：條目資訊 + 現有 prompt。"""
    return (
        f"條目：{row.front}\n"
        f"釋義：{row.back}\n"
        f"例句：{row.example}\n\n"
        f"目前的 image_prompt：\n{row.image_prompt}"
    )


def clean(reply: str) -> str:
    """模型偶爾會加引號或 code fence，清掉；並確保後綴完整。"""
    text = reply.strip().strip("`").strip()
    if text.lower().startswith("image_prompt"):
        text = text.split(":", 1)[-1].strip()
    text = text.strip('"').strip("'").strip()
    text = text.split("\n")[0].strip().rstrip(".")
    if not text.endswith(STYLE_SUFFIX.lstrip(", ")):
        base = text.split(", cinematic lighting")[0].rstrip(", ")
        text = base + STYLE_SUFFIX
    return text


async def main() -> int:
    parser = argparse.ArgumentParser(description="改寫聯想圖 prompt，避開文字道具")
    parser.add_argument("work", type=Path, help="中間 CSV 路徑")
    parser.add_argument("--dry-run", action="store_true", help="只列出會改哪些卡")
    parser.add_argument("--all", action="store_true", help="不篩選，全部重寫")
    parser.add_argument("--card-ids", help="只處理這些 card_id（逗號分隔）")
    args = parser.parse_args()

    settings = load_settings()
    store = CardStore(args.work)
    if not store.exists():
        print(f"找不到工作檔：{args.work}")
        return 1

    rows = await store.read()
    wanted = set(args.card_ids.split(",")) if args.card_ids else None
    targets = [
        row
        for row in rows
        if row.card_id
        and row.image_prompt
        and (wanted is None or row.card_id in wanted)
        and (args.all or has_text_prop(row.image_prompt))
    ]

    print(f"{len(targets)}／{sum(1 for r in rows if r.card_id)} 張卡要改寫")
    if args.dry_run:
        for row in targets[:20]:
            props = "、".join(has_text_prop(row.image_prompt))
            print(f"  {row.card_id} {row.front[:16]:16s} ← {props}")
        if len(targets) > 20:
            print(f"  …另外 {len(targets) - 20} 張")
        return 0
    if not targets:
        return 0

    client = LLMClient(settings.agent_factory.yaml_settings_file)
    changed = 0
    for index, row in enumerate(targets, 1):
        before = row.image_prompt
        try:
            reply = await client.run_agent(AGENT, build_input(row))
        except Exception as error:  # noqa: BLE001 - 單列失敗不該中斷整批（約束 3）
            print(f"  ⚠️ {row.card_id}：{type(error).__name__}: {error}")
            continue

        after = clean(str(reply))
        if after and after != before:
            row.image_prompt = after
            row.image_status = StageStatus.PENDING
            row.image_error = ""
            changed += 1
        # 這個標記只是提醒，不是判決：模型常保留名詞但改成安全的用法
        # （`closed books`、`blank paper documents`），那些不需要再改
        remaining = has_text_prop(row.image_prompt)
        flag = f"（仍含 {'、'.join(remaining)}，看一眼是否安全）" if remaining else ""
        print(f"  [{index}/{len(targets)}] {row.card_id} {flag}")

    if changed:
        await store.write(rows)
    print(f"已改寫 {changed} 張，並把它們的 image_status 設回 pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
