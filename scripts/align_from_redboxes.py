#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def box(item: dict[str, Any]) -> list[float]:
    raw = item.get("bbox") or [item.get("x"), item.get("y"), item.get("w"), item.get("h")]
    return [float(v) for v in raw]


def set_box(item: dict[str, Any], b: list[float]) -> None:
    item["x"], item["y"], item["w"], item["h"] = [round(v, 3) for v in b]


def main() -> None:
    parser = argparse.ArgumentParser(description="Align manifest elements from red-box alignment groups.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tolerance", type=float, default=6.0)
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    rules = load_json(args.rules)
    elements = {str(e.get("id")): e for e in manifest.get("elements", [])}
    changed = []
    for rule in rules.get("alignment_rules", []):
        ids = [str(v) for v in rule.get("element_ids", []) if str(v) in elements]
        if len(ids) < 2:
            continue
        boxes = [box(elements[i]) for i in ids]
        mode = rule.get("type")
        if mode == "center_x":
            target = mean([b[0] + b[2] / 2 for b in boxes])
            for i, b in zip(ids, boxes):
                b[0] = target - b[2] / 2
                set_box(elements[i], b)
                changed.append(i)
        elif mode == "center_y":
            target = mean([b[1] + b[3] / 2 for b in boxes])
            for i, b in zip(ids, boxes):
                b[1] = target - b[3] / 2
                set_box(elements[i], b)
                changed.append(i)
        elif mode == "edge_left":
            target = mean([b[0] for b in boxes])
            if max(abs(b[0] - target) for b in boxes) <= float(rule.get("tolerance_px", args.tolerance)):
                for i, b in zip(ids, boxes):
                    b[0] = target
                    set_box(elements[i], b)
                    changed.append(i)
        elif mode == "edge_top":
            target = mean([b[1] for b in boxes])
            if max(abs(b[1] - target) for b in boxes) <= float(rule.get("tolerance_px", args.tolerance)):
                for i, b in zip(ids, boxes):
                    b[1] = target
                    set_box(elements[i], b)
                    changed.append(i)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"changed": sorted(set(changed)), "out": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
