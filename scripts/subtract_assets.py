#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


def items_from(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        return data
    for key in ("items", "matches", "redboxes"):
        if isinstance(data.get(key), list):
            return data[key]
    raise ValueError("JSON must be a list or contain items/matches/redboxes")


def bbox(item: dict[str, Any]) -> list[int] | None:
    raw = item.get("bbox") or item.get("anchor_slot")
    if isinstance(raw, list) and len(raw) == 4:
        return [int(round(float(v))) for v in raw]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Create residual by filling matched semantic visual boxes white.")
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--matches", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mask-out", type=Path)
    parser.add_argument("--fill", default="#FFFFFF")
    args = parser.parse_args()

    image = Image.open(args.reference).convert("RGB")
    mask = Image.new("RGB", image.size, "black")
    draw = ImageDraw.Draw(image)
    mask_draw = ImageDraw.Draw(mask)
    count = 0
    for item in items_from(args.matches):
        box = bbox(item)
        if not box:
            continue
        x, y, w, h = box
        draw.rectangle([x, y, x + w, y + h], fill=args.fill)
        mask_draw.rectangle([x, y, x + w, y + h], fill="white")
        count += 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    if args.mask_out:
        args.mask_out.parent.mkdir(parents=True, exist_ok=True)
        mask.save(args.mask_out)
    print(json.dumps({"subtracted": count, "out": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
