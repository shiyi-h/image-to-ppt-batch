#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import OrderedDict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


DEFAULT_MAX_SIMPLE = 36
DEFAULT_MAX_SENSITIVE = 4
DEFAULT_MIN_CELL = 160
DEFAULT_MAX_COLS = 6
DEFAULT_MAX_ROWS = 6
DEFAULT_BACKGROUND = "#FF00FF"


def choose_grid(
    count: int,
    *,
    width: int = 1536,
    height: int = 1024,
    min_cell: int = DEFAULT_MIN_CELL,
    max_cols: int = DEFAULT_MAX_COLS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> tuple[int, int]:
    if count < 1:
        raise ValueError("count must be positive")
    candidates: list[tuple[float, int, int]] = []
    for cols in range(1, min(count, max_cols) + 1):
        rows = math.ceil(count / cols)
        if rows > max_rows:
            continue
        cell_w, cell_h = width / cols, height / rows
        if min(cell_w, cell_h) < min_cell:
            continue
        blanks = rows * cols - count
        aspect_penalty = abs(math.log(cell_w / cell_h))
        candidates.append((blanks * 2 + aspect_penalty, rows, cols))
    if not candidates:
        raise ValueError(f"cannot fit {count} cells at minimum {min_cell}px")
    _, rows, cols = min(candidates)
    return rows, cols


def plan_batches(
    items: list[dict[str, Any]],
    *,
    max_simple: int = DEFAULT_MAX_SIMPLE,
    max_sensitive: int = DEFAULT_MAX_SENSITIVE,
    min_cell: int = DEFAULT_MIN_CELL,
    max_cols: int = DEFAULT_MAX_COLS,
    max_rows: int = DEFAULT_MAX_ROWS,
    group_simple_by_style: bool = False,
) -> list[dict[str, Any]]:
    groups: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()
    for item in items:
        sensitivity = infer_sensitivity(item)
        if sensitivity not in {"simple", "sensitive"}:
            raise ValueError(f"{item.get('id')}: sensitivity must be simple or sensitive")
        explicit_batch_key = item.get("batch_key")
        if explicit_batch_key is not None:
            group_key = str(explicit_batch_key)
        elif sensitivity == "simple" and not group_simple_by_style:
            group_key = "simple-dense"
        else:
            group_key = str(item.get("style_key", "default"))
        groups.setdefault((sensitivity, group_key), []).append(item)

    batches: list[dict[str, Any]] = []
    for (sensitivity, style_key), group in groups.items():
        limit = max_simple if sensitivity == "simple" else max_sensitive
        for offset in range(0, len(group), limit):
            chunk = group[offset : offset + limit]
            rows, cols = choose_grid(len(chunk), min_cell=min_cell, max_cols=max_cols, max_rows=max_rows)
            batch_id = f"batch_{len(batches) + 1:03d}"
            style_keys = sorted({str(item.get("style_key", "default")) for item in chunk})
            batches.append(
                {
                    "batch_id": batch_id,
                    "sensitivity": sensitivity,
                    "style_key": group_key,
                    "style_keys": style_keys,
                    "rows": rows,
                    "cols": cols,
                    "blank_cells": rows * cols - len(chunk),
                    "items": chunk,
                    "background": DEFAULT_BACKGROUND,
                    "reference_input_count": 2,
                    "reference_roles": ["full_reference", "numbered_crop_contact_sheet"],
                    "contact_sheet": f"contact_sheets/{batch_id}.png",
                    "generated_grid": f"generated/{batch_id}.png",
                }
            )
    return batches


def infer_sensitivity(item: dict[str, Any]) -> str:
    if item.get("sensitivity") is not None:
        return str(item["sensitivity"])
    identity = " ".join(str(item.get(key, "")) for key in ("id", "label", "kind")).lower()
    keywords = ("logo", "brand", "wordmark", "logotype", "标识", "字标", "品牌")
    bbox = item.get("bbox") or [0, 0, 1, 1]
    width, height = max(float(bbox[2]), 1), max(float(bbox[3]), 1)
    extreme_aspect = max(width / height, height / width) >= 3
    return "sensitive" if item.get("allow_text") or any(word in identity for word in keywords) or extreme_aspect else "simple"


def create_contact_sheet(
    reference_path: Path,
    batch: dict[str, Any],
    out_path: Path,
    *,
    width: int = 1536,
    height: int = 1024,
    pad: int = 6,
) -> list[dict[str, Any]]:
    reference = Image.open(reference_path).convert("RGBA")
    rows, cols = int(batch["rows"]), int(batch["cols"])
    cell_w, cell_h = width / cols, height / rows
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    records = []
    for index, item in enumerate(batch["items"]):
        row, col = divmod(index, cols)
        left, top = round(col * cell_w), round(row * cell_h)
        right, bottom = round((col + 1) * cell_w), round((row + 1) * cell_h)
        draw.rectangle((left + 2, top + 2, right - 2, bottom - 2), outline="#D4DCE8", width=2)
        draw.text((left + 10, top + 8), f"{index + 1}. {item['id']}", fill="#26364D", font=font)
        x, y, w, h = [int(round(float(value))) for value in item["bbox"]]
        crop_box = (
            max(0, x - pad),
            max(0, y - pad),
            min(reference.width, x + w + pad),
            min(reference.height, y + h + pad),
        )
        crop = reference.crop(crop_box)
        crop.thumbnail((max(1, right - left - 48), max(1, bottom - top - 70)), Image.Resampling.LANCZOS)
        paste_x = left + (right - left - crop.width) // 2
        paste_y = top + 48 + (bottom - top - 58 - crop.height) // 2
        sheet.paste(crop, (paste_x, paste_y), crop)
        records.append(
            {
                "id": item["id"],
                "cell_index": index,
                "cell_bbox": [left, top, right - left, bottom - top],
                "source_bbox": [x, y, w, h],
                "crop_bbox": [crop_box[0], crop_box[1], crop_box[2] - crop_box[0], crop_box[3] - crop_box[1]],
            }
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return records


def build_prompt(batch: dict[str, Any], reference_path: Path, contact_path: Path) -> dict[str, Any]:
    objects = "\n".join(
        f"{index + 1}. {item.get('label') or item['id']}"
        for index, item in enumerate(batch["items"])
    )
    rows, cols = int(batch["rows"]), int(batch["cols"])
    blank_cells = int(batch.get("blank_cells", rows * cols - len(batch["items"])))
    text_rule = (
        "Preserve readable text only for items explicitly described as logos or wordmarks."
        if batch.get("sensitivity") == "sensitive"
        else "No readable text, letters, numbers, or watermark."
    )
    prompt = (
        "Create one isolated asset grid for an editable PowerPoint visual replica.\n"
        "Use the full slide reference for global style and the numbered crop contact sheet for exact object identity and order.\n"
        f"Objects in order:\n{objects}\n"
        f"Grid: EXACT {rows} rows x {cols} columns. Cells read left-to-right, then top-to-bottom. "
        "Use equal full-canvas cells with one centered object per occupied cell.\n"
    )
    prompt += f"Leave the final {blank_cells} cells completely blank." if blank_cells else "Fill every cell with exactly one listed object."
    prompt += (
        f"\nBackground: perfectly uniform {batch.get('background', '#00FF00')} chroma key. "
        "No gradients, texture, shadows, floor, panels, labels, captions, dividers, or cell borders.\n"
        "Use no cell borders and no visual separators of any kind.\n"
        "Keep every object fully inside its cell with at least 12% clear padding on all four sides. "
        "Never let an object cross a cell boundary.\n"
        f"{text_rule}\n"
        "The ID labels in the contact sheet are mapping aids only; never reproduce those labels."
    )
    return {
        "batch_id": batch["batch_id"],
        "source_anchor_ids": [str(item["id"]) for item in batch["items"]],
        "reference_inputs": [
            {"role": "full_reference", "path": str(reference_path)},
            {"role": "numbered_crop_contact_sheet", "path": str(contact_path)},
        ],
        "grid": {"rows": rows, "cols": cols, "background": batch.get("background", "#00FF00")},
        "prompt": prompt,
    }


def parse_color(value: str) -> tuple[int, int, int]:
    value = value.strip().removeprefix("#")
    if len(value) != 6:
        raise ValueError("color must be #RRGGBB")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def remove_key(
    image: Image.Image,
    *,
    key: tuple[int, int, int],
    tolerance: int = 60,
    dominance: int = 35,
) -> Image.Image:
    out = image.convert("RGBA")
    pixels = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, _ = pixels[x, y]
            distance = math.sqrt((r - key[0]) ** 2 + (g - key[1]) ** 2 + (b - key[2]) ** 2)
            keyed = distance <= tolerance
            if key[1] > key[0] and key[1] > key[2]:
                keyed = keyed or (g > 120 and g - max(r, b) >= dominance)
            pixels[x, y] = (r, g, b, 0 if keyed else 255)
    return out


def cut_grid(
    grid_path: Path,
    batch: dict[str, Any],
    out_dir: Path,
    *,
    background: str | None = None,
    tolerance: int = 60,
    dominance: int = 35,
) -> list[dict[str, Any]]:
    grid = Image.open(grid_path).convert("RGBA")
    rows, cols = int(batch["rows"]), int(batch["cols"])
    out_dir.mkdir(parents=True, exist_ok=True)
    key = parse_color(background or str(batch.get("background", "#00FF00")))
    records = []
    for index, item in enumerate(batch["items"]):
        row, col = divmod(index, cols)
        left = round(col * grid.width / cols)
        top = round(row * grid.height / rows)
        right = round((col + 1) * grid.width / cols)
        bottom = round((row + 1) * grid.height / rows)
        transparent = remove_key(grid.crop((left, top, right, bottom)), key=key, tolerance=tolerance, dominance=dominance)
        alpha = transparent.getchannel("A")
        alpha_bbox = alpha.getbbox()
        opaque_pixels = sum(alpha.histogram()[1:])
        if alpha_bbox:
            ax1, ay1, ax2, ay2 = alpha_bbox
            edge_clearance = min(ax1, ay1, transparent.width - ax2, transparent.height - ay2)
            trimmed = transparent.crop(alpha_bbox)
            final = Image.new("RGBA", (trimmed.width + 4, trimmed.height + 4), (0, 0, 0, 0))
            final.paste(trimmed, (2, 2), trimmed)
        else:
            edge_clearance = -1
            final = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        out = out_dir / f"{item['id']}.png"
        final.save(out)
        records.append(
            {
                "id": item["id"],
                "batch_id": batch["batch_id"],
                "cell_index": index,
                "cell_bbox": [left, top, right - left, bottom - top],
                "path": str(out),
                "size": [final.width, final.height],
                "opaque_pixels": opaque_pixels,
                "edge_clearance": edge_clearance,
            }
        )
    return records


def validate_cut_assets(
    records: list[dict[str, Any]],
    *,
    edge_margin: int = 2,
    min_opaque_pixels: int = 25,
) -> dict[str, Any]:
    issues = []
    for record in records:
        reasons = []
        if int(record.get("opaque_pixels", 0)) < min_opaque_pixels:
            reasons.append("empty_or_too_small")
        if int(record.get("edge_clearance", -1)) < edge_margin:
            reasons.append("edge_touch")
        path = Path(str(record["path"]))
        if not path.exists():
            reasons.append("missing_file")
        else:
            with Image.open(path) as asset:
                if asset.mode != "RGBA" or asset.getchannel("A").getextrema()[0] != 0:
                    reasons.append("missing_transparency")
        if reasons:
            issues.append({"id": record["id"], "batch_id": record["batch_id"], "reasons": reasons})
    return {
        "status": "pass" if not issues else "fail",
        "assets": len(records),
        "issues": issues,
        "failed_batch_ids": sorted({issue["batch_id"] for issue in issues}),
    }


def split_batch(plan: dict[str, Any], batch_id: str) -> dict[str, Any]:
    updated = json.loads(json.dumps(plan, ensure_ascii=False))
    batches = updated["batches"]
    for index, batch in enumerate(batches):
        if batch["batch_id"] != batch_id:
            continue
        items = batch["items"]
        if len(items) < 2:
            raise ValueError(f"cannot split single-item batch: {batch_id}")
        midpoint = (len(items) + 1) // 2
        children = []
        for suffix, child_items in zip(("a", "b"), (items[:midpoint], items[midpoint:])):
            rows, cols = choose_grid(len(child_items))
            child_id = f"{batch_id}{suffix}"
            child = {
                key: value
                for key, value in batch.items()
                if key not in {"items", "rows", "cols", "blank_cells", "contact_records", "contact_sheet", "generated_grid"}
            }
            child.update(
                {
                    "batch_id": child_id,
                    "rows": rows,
                    "cols": cols,
                    "blank_cells": rows * cols - len(child_items),
                    "items": child_items,
                    "reference_input_count": 2,
                    "reference_roles": ["full_reference", "numbered_crop_contact_sheet"],
                    "contact_sheet": f"contact_sheets/{child_id}.png",
                    "generated_grid": f"generated/{child_id}.png",
                }
            )
            children.append(child)
        batches[index : index + 1] = children
        return updated
    raise ValueError(f"batch not found: {batch_id}")


def load_items(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        return data
    for key in ("visuals", "items", "anchors"):
        if isinstance(data.get(key), list):
            return data[key]
    raise ValueError("inventory must be a list or contain visuals/items/anchors")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def command_plan(args: argparse.Namespace) -> None:
    batches = plan_batches(
        load_items(args.inventory),
        max_simple=args.max_simple,
        max_sensitive=args.max_sensitive,
        min_cell=args.min_cell,
        max_cols=args.max_cols,
        max_rows=args.max_rows,
        group_simple_by_style=args.group_simple_by_style,
    )
    write_json(args.out, {"reference": str(args.reference), "batches": batches})
    print(json.dumps({"batches": len(batches), "generation_calls": len(batches)}, ensure_ascii=False))


def command_contact_sheets(args: argparse.Namespace) -> None:
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    for batch in plan["batches"]:
        out = args.out_dir / f"{batch['batch_id']}.png"
        batch["contact_sheet"] = str(out)
        batch["contact_records"] = create_contact_sheet(
            args.reference, batch, out, width=args.width, height=args.height, pad=args.pad
        )
    write_json(args.plan, plan)
    print(json.dumps({"contact_sheets": len(plan["batches"]), "out_dir": str(args.out_dir)}, ensure_ascii=False))


def command_prompts(args: argparse.Namespace) -> None:
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    rows = []
    for batch in plan["batches"]:
        contact = Path(str(batch["contact_sheet"]))
        rows.append(build_prompt(batch, args.reference, contact))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    print(json.dumps({"prompt_rows": len(rows), "out": str(args.out)}, ensure_ascii=False))


def find_batch(plan_path: Path, batch_id: str) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    for batch in plan["batches"]:
        if batch["batch_id"] == batch_id:
            return batch
    raise ValueError(f"batch not found: {batch_id}")


def command_cut(args: argparse.Namespace) -> None:
    records = cut_grid(
        args.grid,
        find_batch(args.plan, args.batch_id),
        args.out_dir,
        background=args.background,
        tolerance=args.tolerance,
        dominance=args.dominance,
    )
    write_json(args.manifest_out, {"assets": records})
    print(json.dumps({"assets": len(records), "manifest": str(args.manifest_out)}, ensure_ascii=False))


def command_validate(args: argparse.Namespace) -> None:
    data = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    report = validate_cut_assets(data["assets"], edge_margin=args.edge_margin, min_opaque_pixels=args.min_opaque_pixels)
    write_json(args.out, report)
    print(json.dumps(report, ensure_ascii=False))


def command_split(args: argparse.Namespace) -> None:
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    updated = split_batch(plan, args.batch_id)
    write_json(args.plan, updated)
    print(json.dumps({"split": args.batch_id, "batches": len(updated["batches"])}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan dense imagegen icon batches and build crop contact sheets.")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--inventory", required=True, type=Path)
    plan.add_argument("--reference", required=True, type=Path)
    plan.add_argument("--out", required=True, type=Path)
    plan.add_argument("--max-simple", type=int, default=DEFAULT_MAX_SIMPLE)
    plan.add_argument("--max-sensitive", type=int, default=DEFAULT_MAX_SENSITIVE)
    plan.add_argument("--min-cell", type=int, default=DEFAULT_MIN_CELL)
    plan.add_argument("--max-cols", type=int, default=DEFAULT_MAX_COLS)
    plan.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    plan.add_argument(
        "--group-simple-by-style",
        action="store_true",
        help="Use legacy behavior: split simple items by style_key instead of dense-packing them together.",
    )
    plan.set_defaults(func=command_plan)

    sheets = sub.add_parser("contact-sheets")
    sheets.add_argument("--reference", required=True, type=Path)
    sheets.add_argument("--plan", required=True, type=Path)
    sheets.add_argument("--out-dir", required=True, type=Path)
    sheets.add_argument("--width", type=int, default=1536)
    sheets.add_argument("--height", type=int, default=1024)
    sheets.add_argument("--pad", type=int, default=6)
    sheets.set_defaults(func=command_contact_sheets)

    prompts = sub.add_parser("prompts")
    prompts.add_argument("--reference", required=True, type=Path)
    prompts.add_argument("--plan", required=True, type=Path)
    prompts.add_argument("--out", required=True, type=Path)
    prompts.set_defaults(func=command_prompts)

    cut = sub.add_parser("cut")
    cut.add_argument("--plan", required=True, type=Path)
    cut.add_argument("--batch-id", required=True)
    cut.add_argument("--grid", required=True, type=Path)
    cut.add_argument("--out-dir", required=True, type=Path)
    cut.add_argument("--manifest-out", required=True, type=Path)
    cut.add_argument("--background")
    cut.add_argument("--tolerance", type=int, default=60)
    cut.add_argument("--dominance", type=int, default=35)
    cut.set_defaults(func=command_cut)

    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", required=True, type=Path)
    validate.add_argument("--out", required=True, type=Path)
    validate.add_argument("--edge-margin", type=int, default=2)
    validate.add_argument("--min-opaque-pixels", type=int, default=25)
    validate.set_defaults(func=command_validate)

    split = sub.add_parser("split-batch")
    split.add_argument("--plan", required=True, type=Path)
    split.add_argument("--batch-id", required=True)
    split.set_defaults(func=command_split)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
