from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import provider_minimal_test
import image_edit_provider


def tiny_png_base64() -> str:
    import base64
    from io import BytesIO

    buffer = BytesIO()
    Image.new("RGBA", (8, 8), (0, 255, 0, 255)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class ProviderMinimalTest(unittest.TestCase):
    def test_run_minimal_provider_test_uses_one_generated_input_and_redacts_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            env = tmpdir / "provider.env"
            env.write_text(
                "\n".join(
                    [
                        "IMAGE_API_ENDPOINT=https://example.test/v1/images/edits",
                        "IMAGE_API_TOKEN=super-secret-token",
                        "IMAGE_API_MODEL=test-image-model",
                        "IMAGE_API_TRANSPORT=urllib",
                    ]
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_call(config, prompt, image_paths, out_path):
                calls.append((config, prompt, image_paths, out_path))
                Image.new("RGBA", (8, 8), (0, 255, 0, 255)).save(out_path)
                return {
                    "endpoint": config["IMAGE_API_ENDPOINT"],
                    "model": config["IMAGE_API_MODEL"],
                    "output": str(out_path),
                    "response_kind": "base64",
                    "bytes": out_path.stat().st_size,
                    "input_images": len(image_paths),
                    "transport": config["IMAGE_API_TRANSPORT"],
                    "prompt_sha256": "abc123",
                }

            result = provider_minimal_test.run_minimal_provider_test(
                env,
                tmpdir / "out",
                call_func=fake_call,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(len(calls), 1)
            self.assertTrue(Path(result["input_image"]).is_file())
            self.assertTrue(Path(result["output_image"]).is_file())
            self.assertTrue(Path(result["result_json"]).is_file())
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("super-secret-token", serialized)
            self.assertTrue(result["provider_summary"]["token_configured"])
            self.assertTrue(Path(result["provider_test_record"]).is_file())

    def test_run_minimal_provider_test_returns_redacted_failure_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            env = tmpdir / "provider.env"
            env.write_text(
                "\n".join(
                    [
                        "IMAGE_API_ENDPOINT=https://example.test/v1/images/edits",
                        "IMAGE_API_TOKEN=super-secret-token",
                        "IMAGE_API_MODEL=test-image-model",
                        "IMAGE_API_TRANSPORT=urllib",
                    ]
                ),
                encoding="utf-8",
            )

            def fake_call(config, prompt, image_paths, out_path):
                raise RuntimeError("curl failed: 503 using token super-secret-token")

            result = provider_minimal_test.run_minimal_provider_test(
                env,
                tmpdir / "out",
                call_func=fake_call,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["error"]["type"], "RuntimeError")
            self.assertTrue(result["local_draw_decision_required"])
            self.assertEqual(result["next_action"], "ask_user_whether_to_use_local_draw")
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("super-secret-token", serialized)
            self.assertIn("[REDACTED]", serialized)
            self.assertTrue(Path(result["result_json"]).is_file())
            self.assertTrue(Path(result["provider_test_record"]).is_file())

    def test_run_minimal_provider_test_reuses_record_when_provider_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            env = tmpdir / "provider.env"
            env.write_text(
                "\n".join(
                    [
                        "IMAGE_API_ENDPOINT=https://example.test/v1/images/edits",
                        "IMAGE_API_TOKEN=super-secret-token",
                        "IMAGE_API_MODEL=test-image-model",
                        "IMAGE_API_TRANSPORT=urllib",
                    ]
                ),
                encoding="utf-8",
            )
            calls = []

            def passing_call(config, prompt, image_paths, out_path):
                calls.append("pass")
                Image.new("RGBA", (8, 8), (0, 255, 0, 255)).save(out_path)
                return {
                    "endpoint": config["IMAGE_API_ENDPOINT"],
                    "model": config["IMAGE_API_MODEL"],
                    "output": str(out_path),
                    "response_kind": "base64",
                    "bytes": out_path.stat().st_size,
                    "input_images": len(image_paths),
                    "transport": config["IMAGE_API_TRANSPORT"],
                    "prompt_sha256": "abc123",
                }

            first = provider_minimal_test.run_minimal_provider_test(
                env,
                tmpdir / "out1",
                call_func=passing_call,
            )

            def failing_call(config, prompt, image_paths, out_path):
                calls.append("fail")
                raise AssertionError("cached provider should not call API")

            second = provider_minimal_test.run_minimal_provider_test(
                env,
                tmpdir / "out2",
                call_func=failing_call,
            )

            self.assertEqual(first["status"], "pass")
            self.assertEqual(second["status"], "pass")
            self.assertTrue(second["cached"])
            self.assertEqual(calls, ["pass"])
            self.assertTrue(Path(second["result_json"]).is_file())

    def test_run_minimal_provider_test_reruns_when_provider_record_fingerprint_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            env = tmpdir / "provider.env"
            env.write_text(
                "\n".join(
                    [
                        "IMAGE_API_ENDPOINT=https://example.test/v1/images/edits",
                        "IMAGE_API_TOKEN=super-secret-token",
                        "IMAGE_API_MODEL=test-image-model",
                        "IMAGE_API_TRANSPORT=urllib",
                    ]
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_call(config, prompt, image_paths, out_path):
                calls.append(config["IMAGE_API_MODEL"])
                Image.new("RGBA", (8, 8), (0, 255, 0, 255)).save(out_path)
                return {
                    "endpoint": config["IMAGE_API_ENDPOINT"],
                    "model": config["IMAGE_API_MODEL"],
                    "output": str(out_path),
                    "response_kind": "base64",
                    "bytes": out_path.stat().st_size,
                    "input_images": len(image_paths),
                    "transport": config["IMAGE_API_TRANSPORT"],
                    "prompt_sha256": "abc123",
                }

            provider_minimal_test.run_minimal_provider_test(env, tmpdir / "out1", call_func=fake_call)
            env.write_text(
                "\n".join(
                    [
                        "IMAGE_API_ENDPOINT=https://example.test/v1/images/edits",
                        "IMAGE_API_TOKEN=super-secret-token",
                        "IMAGE_API_MODEL=changed-image-model",
                        "IMAGE_API_TRANSPORT=urllib",
                    ]
                ),
                encoding="utf-8",
            )
            second = provider_minimal_test.run_minimal_provider_test(env, tmpdir / "out2", call_func=fake_call)

            self.assertFalse(second["cached"])
            self.assertEqual(calls, ["test-image-model", "changed-image-model"])

    def test_call_image_edit_retries_curl_failures_until_third_attempt_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            input_image = tmpdir / "input.png"
            output_image = tmpdir / "output.png"
            Image.new("RGB", (16, 16), "white").save(input_image)
            config = {
                **image_edit_provider.DEFAULTS,
                "IMAGE_API_ENDPOINT": "https://example.test/v1/images/edits",
                "IMAGE_API_TOKEN": "super-secret-token",
                "IMAGE_API_MODEL": "test-image-model",
                "IMAGE_API_TRANSPORT": "curl",
            }
            calls = []

            def fake_runner(args, input, stdout, stderr, timeout, check):
                calls.append(args)
                if len(calls) < 3:
                    return subprocess.CompletedProcess(args, 22, stdout=b"", stderr=b"curl: (22) The requested URL returned error: 503")
                payload = json.dumps({"data": [{"b64_json": tiny_png_base64()}]}).encode("utf-8")
                return subprocess.CompletedProcess(args, 0, stdout=payload, stderr=b"")

            result = image_edit_provider.call_image_edit(
                config,
                "make a tiny image",
                [input_image],
                output_image,
                runner_func=fake_runner,
            )

            self.assertEqual(len(calls), 3)
            self.assertEqual(result["attempts"], 3)
            self.assertTrue(output_image.is_file())

    def test_call_image_edit_raises_after_three_failed_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            input_image = tmpdir / "input.png"
            output_image = tmpdir / "output.png"
            Image.new("RGB", (16, 16), "white").save(input_image)
            config = {
                **image_edit_provider.DEFAULTS,
                "IMAGE_API_ENDPOINT": "https://example.test/v1/images/edits",
                "IMAGE_API_TOKEN": "super-secret-token",
                "IMAGE_API_MODEL": "test-image-model",
                "IMAGE_API_TRANSPORT": "curl",
            }
            calls = []

            def fake_runner(args, input, stdout, stderr, timeout, check):
                calls.append(args)
                return subprocess.CompletedProcess(args, 22, stdout=b"", stderr=b"curl: (22) The requested URL returned error: 503")

            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                image_edit_provider.call_image_edit(
                    config,
                    "make a tiny image",
                    [input_image],
                    output_image,
                    runner_func=fake_runner,
                )

            self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
