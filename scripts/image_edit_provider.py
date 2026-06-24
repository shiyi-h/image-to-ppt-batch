#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import subprocess
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from PIL import Image, ImageDraw, ImageFont


DEFAULTS = {
    "IMAGE_API_SIZE": "1024x1024",
    "IMAGE_API_QUALITY": "high",
    "IMAGE_API_OUTPUT_FORMAT": "png",
    "IMAGE_API_RESPONSE_FORMAT": "url",
    "IMAGE_API_TRANSPORT": "curl",
    "IMAGE_API_CONNECT_TIMEOUT_SECONDS": "15",
    "IMAGE_API_TOTAL_TIMEOUT_SECONDS": "300",
    "IMAGE_API_TIMEOUT_SECONDS": "300",
    "IMAGE_API_PROXY_MODE": "direct",
    "IMAGE_API_IMAGE_FIELD": "image",
    "IMAGE_API_MULTI_IMAGE_MODE": "repeat",
    "IMAGE_API_EXTRA_FIELDS_JSON": "{}",
}
REQUIRED = ("IMAGE_API_ENDPOINT", "IMAGE_API_TOKEN", "IMAGE_API_MODEL")
PLACEHOLDERS = {"", "xxx", "replace_me", "changeme", "your_token", "你的令牌"}
MAX_IMAGE_API_ATTEMPTS = 3


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid env line: {raw_line}")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    for key in set(DEFAULTS) | set(REQUIRED):
        if key in os.environ:
            values[key] = os.environ[key]
    return {**DEFAULTS, **values}


def validate_config(config: dict[str, str]) -> None:
    missing = missing_required_keys(config)
    if missing:
        raise ValueError("missing provider configuration: " + ", ".join(missing))
    endpoint = str(config["IMAGE_API_ENDPOINT"])
    if not endpoint.startswith(("https://", "http://")):
        raise ValueError("IMAGE_API_ENDPOINT must be an HTTP(S) URL")
    if config.get("IMAGE_API_TRANSPORT", "urllib") not in {"curl", "urllib"}:
        raise ValueError("IMAGE_API_TRANSPORT must be curl or urllib")
    if config.get("IMAGE_API_PROXY_MODE", "direct") not in {"direct", "environment"}:
        raise ValueError("IMAGE_API_PROXY_MODE must be direct or environment")
    for key, fallback in (
        ("IMAGE_API_CONNECT_TIMEOUT_SECONDS", "15"),
        ("IMAGE_API_TOTAL_TIMEOUT_SECONDS", config.get("IMAGE_API_TIMEOUT_SECONDS", "300")),
    ):
        try:
            timeout = float(config.get(key, fallback))
        except ValueError as exc:
            raise ValueError(f"{key} must be numeric") from exc
        if timeout <= 0:
            raise ValueError(f"{key} must be positive")
    if config.get("IMAGE_API_MULTI_IMAGE_MODE", "repeat") not in {"repeat", "indexed"}:
        raise ValueError("IMAGE_API_MULTI_IMAGE_MODE must be repeat or indexed")
    try:
        extra = json.loads(config.get("IMAGE_API_EXTRA_FIELDS_JSON", "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("IMAGE_API_EXTRA_FIELDS_JSON must be valid JSON") from exc
    if not isinstance(extra, dict):
        raise ValueError("IMAGE_API_EXTRA_FIELDS_JSON must contain a JSON object")


def missing_required_keys(config: dict[str, str]) -> list[str]:
    return [key for key in REQUIRED if str(config.get(key, "")).strip().lower() in PLACEHOLDERS]


def normalize_request_text(request_text: str) -> str:
    return "".join(request_text.lower().split())


def infer_provider_override(request_text: str) -> str | None:
    normalized = normalize_request_text(request_text)
    if not normalized:
        return None
    codex_markers = (
        "codeximagegen",
        "内置imagegen",
        "默认imagegen",
        "用imagegen",
        "使用imagegen",
        "走imagegen",
        "不用api",
        "不要api",
        "不走api",
        "不调用api",
        "不用外部api",
        "不要外部api",
        "不用外部接口",
        "不要外部接口",
    )
    if any(marker in normalized for marker in codex_markers):
        return "codex-imagegen"
    return None


def resolve_provider_status(env_path: Path, request_text: str = "") -> dict[str, Any]:
    """Return the generation provider without exposing secrets.

    Missing provider.env or missing core credentials is not an error for the
    PPT workflow: it means the agent should use the built-in Codex imagegen
    tool. Malformed non-secret options still raise through validate_config so
    an intentionally configured API does not fail silently.
    """
    provider_override = infer_provider_override(request_text)
    if provider_override == "codex-imagegen":
        return {
            "provider": "codex-imagegen",
            "api_configured": False,
            "env_path": str(env_path),
            "reason": "explicit user request selected built-in Codex imagegen",
            "missing": [],
            "override": "request_text",
        }
    if not env_path.is_file():
        return {
            "provider": "codex-imagegen",
            "api_configured": False,
            "env_path": str(env_path),
            "reason": "provider.env not found; use built-in Codex imagegen",
            "missing": ["provider.env"],
        }
    config = load_env(env_path)
    missing = missing_required_keys(config)
    if missing:
        return {
            "provider": "codex-imagegen",
            "api_configured": False,
            "env_path": str(env_path),
            "reason": "provider.env is missing required API fields; use built-in Codex imagegen",
            "missing": missing,
            "summary": safe_config_summary(config),
        }
    validate_config(config)
    return {
        "provider": "external-image-api",
        "api_configured": True,
        "env_path": str(env_path),
        "reason": "provider.env contains the required API fields",
        "missing": [],
        "summary": safe_config_summary(config),
    }


def contain(image: Image.Image, width: int, height: int) -> Image.Image:
    result = image.copy()
    result.thumbnail((max(1, width), max(1, height)), Image.Resampling.LANCZOS)
    return result


def compose_provider_input(
    reference_path: Path,
    contact_path: Path,
    out_path: Path,
    *,
    width: int = 1536,
    height: int = 1536,
) -> dict[str, Any]:
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=max(18, width // 80))
    outer = max(16, width // 64)
    header = max(38, height // 28)
    gap = max(16, height // 64)
    panel_height = (height - outer * 2 - gap) // 2
    records = []
    for index, (role, path) in enumerate(
        (("full_reference", reference_path), ("numbered_crop_contact_sheet", contact_path))
    ):
        top = outer + index * (panel_height + gap)
        left, right, bottom = outer, width - outer, top + panel_height
        draw.rectangle((left, top, right, bottom), outline="#B7C7DB", width=2)
        draw.rectangle((left, top, right, top + header), fill="#EEF3F9")
        label = "FULL REFERENCE" if index == 0 else "NUMBERED CROP CONTACT SHEET"
        draw.text((left + 12, top + 9), label, fill="#17365D", font=font)
        with Image.open(path) as source:
            prepared = contain(source.convert("RGBA"), right - left - 24, bottom - top - header - 20)
        paste_x = left + (right - left - prepared.width) // 2
        paste_y = top + header + (bottom - top - header - prepared.height) // 2
        canvas.paste(prepared, (paste_x, paste_y), prepared)
        records.append(
            {
                "role": role,
                "source": str(path),
                "fitted_bbox": [paste_x, paste_y, prepared.width, prepared.height],
            }
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return {
        "path": str(out_path),
        "size": [width, height],
        "reference_role": records[0]["role"],
        "contact_role": records[1]["role"],
        "inputs": records,
    }


def multipart_body(
    fields: list[tuple[str, str]],
    files: list[tuple[str, Path]],
) -> tuple[bytes, str]:
    boundary = f"----CodexPptReplica{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    for field_name, path in files:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'.encode(),
                f"Content-Type: {mime}\r\n\r\n".encode(),
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def find_image_value(payload: Any) -> tuple[str, str] | None:
    if isinstance(payload, dict):
        for key in ("b64_json", "base64", "image_base64"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return "base64", value
        for key in ("url", "image_url"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return "url", value
        for key in ("data", "output", "result", "images"):
            if key in payload:
                found = find_image_value(payload[key])
                if found:
                    return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_image_value(item)
            if found:
                return found
    return None


def extract_image_bytes(payload: Any, *, timeout: float = 180, urlopen_func=urlopen) -> tuple[bytes, str]:
    found = find_image_value(payload)
    if not found:
        raise ValueError("API response did not contain a supported image URL or base64 field")
    kind, value = found
    if kind == "base64":
        try:
            return base64.b64decode(value, validate=True), kind
        except ValueError as exc:
            raise ValueError("API returned invalid base64 image data") from exc
    request = Request(value, headers={"Accept": "image/*", "User-Agent": "Codex-PPT-Replica/1.0"})
    with urlopen_func(request, timeout=timeout) as response:
        return response.read(), kind


def image_field_names(config: dict[str, str], count: int) -> list[str]:
    base = config.get("IMAGE_API_IMAGE_FIELD", "image")
    if config.get("IMAGE_API_MULTI_IMAGE_MODE", "repeat") == "indexed" and count > 1:
        return [f"{base}[{index}]" for index in range(count)]
    return [base] * count


def timeout_values(config: dict[str, str]) -> tuple[float, float]:
    connect = float(config.get("IMAGE_API_CONNECT_TIMEOUT_SECONDS", DEFAULTS["IMAGE_API_CONNECT_TIMEOUT_SECONDS"]))
    total = float(
        config.get(
            "IMAGE_API_TOTAL_TIMEOUT_SECONDS",
            config.get("IMAGE_API_TIMEOUT_SECONDS", DEFAULTS["IMAGE_API_TOTAL_TIMEOUT_SECONDS"]),
        )
    )
    return connect, total


def curl_config_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def post_with_curl(
    config: dict[str, str],
    fields: list[tuple[str, str]],
    files: list[tuple[str, Path]],
    *,
    runner_func=subprocess.run,
) -> bytes:
    connect_timeout, total_timeout = timeout_values(config)
    args = [
        "curl",
        "--location",
        config["IMAGE_API_ENDPOINT"],
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--connect-timeout",
        f"{connect_timeout:g}",
        "--max-time",
        f"{total_timeout:g}",
        "--header",
        "Accept: */*",
        "--config",
        "-",
    ]
    if config.get("IMAGE_API_PROXY_MODE", "direct") == "direct":
        args.extend(("--noproxy", "*"))
    for name, value in fields:
        args.extend(("--form-string", f"{name}={value}"))
    for field_name, path in files:
        args.extend(("--form", f"{field_name}=@{path}"))
    secret_config = (
        f'header = "Authorization: Bearer {curl_config_quote(config["IMAGE_API_TOKEN"])}"\n'
    ).encode("utf-8")
    try:
        completed = runner_func(
            args,
            input=secret_config,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=connect_timeout + total_timeout + 5,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"image API exceeded total timeout of {total_timeout:g}s") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).decode("utf-8", errors="replace")[:1200]
        detail = detail.replace(config["IMAGE_API_TOKEN"], "[REDACTED]")
        raise RuntimeError(f"curl image API request failed with exit {completed.returncode}: {detail}")
    return completed.stdout


def call_image_edit(
    config: dict[str, str],
    prompt: str,
    image_paths: list[Path],
    out_path: Path,
    *,
    urlopen_func=urlopen,
    runner_func=subprocess.run,
) -> dict[str, Any]:
    validate_config(config)
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if not image_paths:
        raise ValueError("at least one input image is required")
    missing = [str(path) for path in image_paths if not path.is_file()]
    if missing:
        raise ValueError("input image not found: " + ", ".join(missing))
    fields = [
        ("model", config["IMAGE_API_MODEL"]),
        ("prompt", prompt),
        ("size", config.get("IMAGE_API_SIZE", DEFAULTS["IMAGE_API_SIZE"])),
        ("quality", config.get("IMAGE_API_QUALITY", DEFAULTS["IMAGE_API_QUALITY"])),
        ("output_format", config.get("IMAGE_API_OUTPUT_FORMAT", DEFAULTS["IMAGE_API_OUTPUT_FORMAT"])),
        ("response_format", config.get("IMAGE_API_RESPONSE_FORMAT", DEFAULTS["IMAGE_API_RESPONSE_FORMAT"])),
    ]
    extra = json.loads(config.get("IMAGE_API_EXTRA_FIELDS_JSON", "{}"))
    fields.extend((str(key), str(value)) for key, value in extra.items())
    files = list(zip(image_field_names(config, len(image_paths)), image_paths))
    transport = config.get("IMAGE_API_TRANSPORT", "urllib")
    _, total_timeout = timeout_values(config)
    body: bytes | None = None
    boundary: str | None = None
    request_headers: dict[str, str] | None = None
    if transport != "curl":
        body, boundary = multipart_body(fields, files)
        request_headers = {
            "Authorization": f"Bearer {config['IMAGE_API_TOKEN']}",
            "Accept": "*/*",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Codex-PPT-Replica/1.0",
        }
    for attempt in range(1, MAX_IMAGE_API_ATTEMPTS + 1):
        try:
            if transport == "curl":
                response_body = post_with_curl(config, fields, files, runner_func=runner_func)
            else:
                request = Request(
                    config["IMAGE_API_ENDPOINT"],
                    data=body,
                    headers=request_headers,
                    method="POST",
                )
                try:
                    with urlopen_func(request, timeout=total_timeout) as response:
                        response_body = response.read()
                except HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")[:1000]
                    raise RuntimeError(f"image API returned HTTP {exc.code}: {detail}") from exc
                except URLError as exc:
                    raise RuntimeError(f"image API request failed: {exc.reason}") from exc
            try:
                payload = json.loads(response_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("image API response was not valid JSON") from exc
            download_open = urlopen_func
            if config.get("IMAGE_API_PROXY_MODE", "direct") == "direct" and urlopen_func is urlopen:
                download_open = build_opener(ProxyHandler({})).open
            image_bytes, response_kind = extract_image_bytes(payload, timeout=total_timeout, urlopen_func=download_open)
            try:
                with Image.open(BytesIO(image_bytes)) as generated:
                    generated.verify()
            except Exception as exc:
                raise RuntimeError("image API response did not resolve to a valid image") from exc
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(image_bytes)
            return {
                "endpoint": config["IMAGE_API_ENDPOINT"],
                "model": config["IMAGE_API_MODEL"],
                "output": str(out_path),
                "response_kind": response_kind,
                "bytes": len(image_bytes),
                "input_images": len(image_paths),
                "transport": transport,
                "attempts": attempt,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            }
        except (RuntimeError, ValueError) as exc:
            if attempt == MAX_IMAGE_API_ATTEMPTS:
                raise RuntimeError(
                    f"image API request failed after {MAX_IMAGE_API_ATTEMPTS} attempts: {exc}"
                ) from exc


def load_prompt_row(prompts_path: Path, batch_id: str) -> dict[str, Any]:
    if not prompts_path.is_file():
        raise ValueError(f"prompt JSONL not found: {prompts_path}")
    for line_no, line in enumerate(prompts_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {prompts_path}:{line_no}") from exc
        if row.get("batch_id") == batch_id:
            if not isinstance(row.get("prompt"), str) or not row["prompt"].strip():
                raise ValueError(f"prompt row {batch_id} is missing prompt text")
            return row
    raise ValueError(f"batch_id not found in prompt JSONL: {batch_id}")


def generate_from_prompt_rows(
    env_path: Path,
    prompts_path: Path,
    batch_id: str,
    image_paths: list[Path],
    out_path: Path,
    *,
    call_func: Callable[[dict[str, str], str, list[Path], Path], dict[str, Any]] = call_image_edit,
) -> dict[str, Any]:
    config = load_env(env_path)
    row = load_prompt_row(prompts_path, batch_id)
    result = call_func(config, row["prompt"], image_paths, out_path)
    return {
        "batch_id": batch_id,
        "source_anchor_ids": row.get("source_anchor_ids", []),
        **result,
    }


def safe_config_summary(config: dict[str, str]) -> dict[str, Any]:
    return {
        "endpoint": config.get("IMAGE_API_ENDPOINT", ""),
        "model": config.get("IMAGE_API_MODEL", ""),
        "token_configured": bool(config.get("IMAGE_API_TOKEN", "")),
        "size": config.get("IMAGE_API_SIZE", ""),
        "quality": config.get("IMAGE_API_QUALITY", ""),
        "response_format": config.get("IMAGE_API_RESPONSE_FORMAT", ""),
        "transport": config.get("IMAGE_API_TRANSPORT", ""),
        "connect_timeout_seconds": config.get("IMAGE_API_CONNECT_TIMEOUT_SECONDS", ""),
        "total_timeout_seconds": config.get("IMAGE_API_TOTAL_TIMEOUT_SECONDS", config.get("IMAGE_API_TIMEOUT_SECONDS", "")),
        "proxy_mode": config.get("IMAGE_API_PROXY_MODE", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Call an OpenAI-compatible multipart image edit API without printing secrets.")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve = sub.add_parser("resolve-provider")
    resolve.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / "provider.env")
    request_group = resolve.add_mutually_exclusive_group()
    request_group.add_argument("--request", default="")
    request_group.add_argument("--request-file", type=Path)

    check = sub.add_parser("check-config")
    check.add_argument("--env", required=True, type=Path)

    compose = sub.add_parser("compose")
    compose.add_argument("--reference", required=True, type=Path)
    compose.add_argument("--contact-sheet", required=True, type=Path)
    compose.add_argument("--out", required=True, type=Path)
    compose.add_argument("--width", type=int, default=1536)
    compose.add_argument("--height", type=int, default=1536)

    generate = sub.add_parser("generate")
    generate.add_argument("--env", required=True, type=Path)
    generate.add_argument("--image", required=True, action="append", type=Path)
    prompt_group = generate.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt")
    prompt_group.add_argument("--prompt-file", type=Path)
    generate.add_argument("--out", required=True, type=Path)

    generate_row = sub.add_parser("generate-row")
    generate_row.add_argument("--env", required=True, type=Path)
    generate_row.add_argument("--prompts", required=True, type=Path)
    generate_row.add_argument("--batch-id", required=True)
    generate_row.add_argument("--image", required=True, action="append", type=Path)
    generate_row.add_argument("--out", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "resolve-provider":
        request_text = args.request_file.read_text(encoding="utf-8") if args.request_file else args.request
        print(json.dumps(resolve_provider_status(args.env, request_text=request_text), ensure_ascii=False, indent=2))
    elif args.command == "check-config":
        config = load_env(args.env)
        validate_config(config)
        print(json.dumps(safe_config_summary(config), ensure_ascii=False, indent=2))
    elif args.command == "compose":
        print(json.dumps(compose_provider_input(args.reference, args.contact_sheet, args.out, width=args.width, height=args.height), ensure_ascii=False, indent=2))
    elif args.command == "generate":
        config = load_env(args.env)
        prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8")
        print(json.dumps(call_image_edit(config, prompt, args.image, args.out), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(generate_from_prompt_rows(args.env, args.prompts, args.batch_id, args.image, args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
