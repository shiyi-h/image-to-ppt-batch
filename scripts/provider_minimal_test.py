#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from image_edit_provider import (
    MAX_IMAGE_API_ATTEMPTS,
    call_image_edit,
    load_env,
    safe_config_summary,
    validate_config,
)


PROMPT = (
    "Use the input image as a simple edit reference. Return one valid PNG image with a clean "
    "blue circle centered on a plain white background. No text, no watermark."
)
PROVIDER_RECORD_NAME = "provider_test_record.json"
FINGERPRINT_KEYS = (
    "IMAGE_API_ENDPOINT",
    "IMAGE_API_MODEL",
    "IMAGE_API_SIZE",
    "IMAGE_API_QUALITY",
    "IMAGE_API_OUTPUT_FORMAT",
    "IMAGE_API_RESPONSE_FORMAT",
    "IMAGE_API_TRANSPORT",
    "IMAGE_API_CONNECT_TIMEOUT_SECONDS",
    "IMAGE_API_TOTAL_TIMEOUT_SECONDS",
    "IMAGE_API_TIMEOUT_SECONDS",
    "IMAGE_API_PROXY_MODE",
    "IMAGE_API_IMAGE_FIELD",
    "IMAGE_API_MULTI_IMAGE_MODE",
    "IMAGE_API_EXTRA_FIELDS_JSON",
)


def create_minimal_input(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((56, 56, 200, 200), outline="#1E73D8", width=8)
    image.save(path)


def verify_image(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        return {"path": str(path), "size": list(image.size), "mode": image.mode}


def redact_message(message: str, config: dict[str, str] | None) -> str:
    if not config:
        return message
    token = config.get("IMAGE_API_TOKEN", "")
    if token:
        message = message.replace(token, "[REDACTED]")
    return message


def write_result(path: Path, result: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def provider_fingerprint(config: dict[str, str]) -> str:
    payload = {key: str(config.get(key, "")) for key in FINGERPRINT_KEYS}
    payload["IMAGE_API_TOKEN_SHA256"] = hashlib.sha256(config.get("IMAGE_API_TOKEN", "").encode("utf-8")).hexdigest()
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_matching_record(record_file: Path, fingerprint: str) -> dict[str, Any] | None:
    if not record_file.is_file():
        return None
    try:
        record = json.loads(record_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if record.get("provider_fingerprint") != fingerprint:
        return None
    if not isinstance(record.get("last_result"), dict):
        return None
    return record


def write_provider_record(
    record_file: Path,
    *,
    fingerprint: str,
    config: dict[str, str],
    result: dict[str, Any],
) -> None:
    record = {
        "provider": "external-image-api",
        "provider_fingerprint": fingerprint,
        "provider_summary": safe_config_summary(config),
        "last_status": result.get("status"),
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "last_result": result,
    }
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def cached_result_from_record(record: dict[str, Any], record_file: Path, result_json: Path) -> dict[str, Any]:
    last_result = dict(record["last_result"])
    result = {
        "status": last_result.get("status", record.get("last_status", "fail")),
        "cached": True,
        "provider": record.get("provider", "external-image-api"),
        "provider_fingerprint": record.get("provider_fingerprint"),
        "provider_summary": record.get("provider_summary", {}),
        "provider_test_record": str(record_file),
        "cached_tested_at": record.get("tested_at"),
        "cached_result": last_result,
        "result_json": str(result_json),
    }
    for key in ("error", "api_attempts", "local_draw_decision_required", "next_action"):
        if key in last_result:
            result[key] = last_result[key]
    return write_result(result_json, result)


def run_minimal_provider_test(
    env_path: Path,
    out_dir: Path,
    *,
    call_func: Callable[[dict[str, str], str, list[Path], Path], dict[str, Any]] = call_image_edit,
    record_file: Path | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    input_image = out_dir / "provider_minimal_input.png"
    output_image = out_dir / "provider_minimal_output.png"
    result_json = out_dir / "provider_minimal_result.json"
    record_file = record_file or env_path.with_name(PROVIDER_RECORD_NAME)
    config: dict[str, str] | None = None
    try:
        config = load_env(env_path)
        validate_config(config)
        fingerprint = provider_fingerprint(config)
        matching_record = load_matching_record(record_file, fingerprint)
        if matching_record:
            return cached_result_from_record(matching_record, record_file, result_json)
        create_minimal_input(input_image)
        api_result = call_func(config, PROMPT, [input_image], output_image)
        output_info = verify_image(output_image)
        result = {
            "status": "pass",
            "cached": False,
            "provider_fingerprint": fingerprint,
            "provider_summary": safe_config_summary(config),
            "provider_test_record": str(record_file),
            "input_image": str(input_image),
            "output_image": str(output_image),
            "output_info": output_info,
            "api_result": api_result,
            "result_json": str(result_json),
        }
        write_provider_record(record_file, fingerprint=fingerprint, config=config, result=result)
        return write_result(result_json, result)
    except Exception as exc:
        local_draw_decision_required = config is not None
        result = {
            "status": "fail",
            "cached": False,
            "provider_summary": safe_config_summary(config) if config else {},
            "provider_test_record": str(record_file),
            "input_image": str(input_image),
            "output_image": str(output_image),
            "result_json": str(result_json),
            "error": {
                "type": type(exc).__name__,
                "message": redact_message(str(exc), config),
            },
            "api_attempts": MAX_IMAGE_API_ATTEMPTS if local_draw_decision_required else 0,
            "local_draw_decision_required": local_draw_decision_required,
            "next_action": (
                "ask_user_whether_to_use_local_draw"
                if local_draw_decision_required
                else "fix_provider_configuration"
            ),
        }
        if config is not None:
            fingerprint = provider_fingerprint(config)
            result["provider_fingerprint"] = fingerprint
            write_provider_record(record_file, fingerprint=fingerprint, config=config, result=result)
        return write_result(result_json, result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one minimal external image provider availability test.")
    parser.add_argument("--env", required=True, type=Path, help="Path to provider.env.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Directory for the test input, output, and JSON result.")
    parser.add_argument(
        "--record-file",
        type=Path,
        help=f"Provider test cache record. Defaults to provider.env sibling {PROVIDER_RECORD_NAME}.",
    )
    args = parser.parse_args()
    result = run_minimal_provider_test(args.env, args.out_dir, record_file=args.record_file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
