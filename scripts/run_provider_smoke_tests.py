#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from image_edit_provider import call_image_edit, load_env, validate_config


CASES = [
    {
        "id": "preserve-local-edit",
        "prompt": (
            "严格保留输入图片的主体、构图、颜色和文字不变，只在右上角添加一枚小号红色圆形印章，"
            "印章内写 DEMO。不要修改其他任何区域。"
        ),
    },
    {
        "id": "ppt-icon-style",
        "prompt": (
            "以输入图片中的主要主体作为身份参考，生成一个单独的企业级蓝色线稿图标。"
            "保持主体语义清晰，深蓝色均匀描边，无文字、无数字、无阴影、无面板。"
            "将图标居中放在完全均匀的 #00FF00 纯绿色背景上，四周至少保留 18% 空白。"
        ),
    },
]


def run_smoke_tests(
    env_path: Path,
    image_path: Path,
    out_dir: Path,
    *,
    call_func: Callable[[dict[str, str], str, list[Path], Path], dict[str, Any]] = call_image_edit,
) -> list[dict[str, Any]]:
    config = load_env(env_path)
    validate_config(config)
    if not image_path.is_file():
        raise ValueError(f"smoke test image not found: {image_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, case in enumerate(CASES, 1):
        out = out_dir / f"{index:02d}-{case['id']}.png"
        total_timeout = config.get("IMAGE_API_TOTAL_TIMEOUT_SECONDS", config.get("IMAGE_API_TIMEOUT_SECONDS", "300"))
        print(f"[{index}/{len(CASES)}] Starting {case['id']} (total timeout {total_timeout}s)...", flush=True)
        api_result = call_func(config, case["prompt"], [image_path], out)
        results.append({"case_id": case["id"], **api_result})
        print(f"[{index}/{len(CASES)}] Saved {out}", flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run two low-volume image edit API smoke tests.")
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run_smoke_tests(args.env, args.image, args.out_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
