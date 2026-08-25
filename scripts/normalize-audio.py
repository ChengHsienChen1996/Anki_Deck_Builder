#!/usr/bin/env python
"""把既有的語音檔就地調成統一響度。

新生成的音檔在 `VoxCPMClient` 寫檔前就已經正規化過；這支腳本是給
**正規化之前就生好的音檔**用的——重跑 `audio --force` 要十幾分鐘，
而且會換掉已經聽過、確認唸得對的內容。

用法：

    uv run python scripts/normalize-audio.py work/media/audio
    uv run python scripts/normalize-audio.py work/media/audio --dry-run

目標響度與峰值上限取自 `.env` 的 `VOXCPM2_LOUDNESS_*`，與生成時同一組設定。
處理是冪等的：跑第二次每個檔案的增益都會是 0.0 dB。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from anki_deck_builder.clients.tts_client import (  # noqa: E402
    WAV_SUBTYPE,
    normalize_loudness,
)
from anki_deck_builder.config import TTSSettings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="就地統一語音檔響度")
    parser.add_argument("directory", type=Path, help="音檔目錄，例如 work/media/audio")
    parser.add_argument("--dry-run", action="store_true", help="只列出會做的調整，不寫檔")
    args = parser.parse_args()

    settings = TTSSettings()
    files = sorted(args.directory.glob("*.wav"))
    if not files:
        print(f"{args.directory} 底下沒有 .wav")
        return 1

    print(
        f"{len(files)} 個檔案 → 目標 {settings.loudness_target_dbfs} dBFS、"
        f"峰值上限 {settings.loudness_peak_dbfs} dBFS"
        + ("（dry-run）" if args.dry_run else "")
    )

    capped: list[str] = []
    gains: list[float] = []
    for path in files:
        audio, rate = sf.read(str(path), dtype="float32")
        adjusted, gain_db, hit_cap = normalize_loudness(
            audio,
            rate,
            target_dbfs=settings.loudness_target_dbfs,
            peak_dbfs=settings.loudness_peak_dbfs,
        )
        gains.append(gain_db)
        if hit_cap:
            capped.append(path.name)
        if not args.dry_run:
            sf.write(str(path), adjusted, rate, subtype=WAV_SUBTYPE)

    print(
        f"增益 最小 {min(gains):+.1f} dB／中位 {sorted(gains)[len(gains) // 2]:+.1f} dB"
        f"／最大 {max(gains):+.1f} dB"
    )
    if capped:
        print(f"\n{len(capped)} 個檔案放大到上限仍偏小聲，多半是生成失敗，建議重跑：")
        for name in capped:
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
