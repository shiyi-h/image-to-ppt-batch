# External Image Edit API

Use this path when `scripts/image_edit_provider.py resolve-provider --env provider.env` returns `external-image-api`.

If `provider.env` is absent, or if any of `IMAGE_API_ENDPOINT`, `IMAGE_API_TOKEN`, or `IMAGE_API_MODEL` is missing or blank, do not use this path. Continue with the built-in Codex imagegen tool.

## Configuration

Fill `provider.env` inside the skill directory. Never print or paste the token into chat. Use `provider.env.example` as a local template if needed.

| Variable | Curl equivalent | Required |
|---|---|---|
| `IMAGE_API_ENDPOINT` | Request URL | Yes |
| `IMAGE_API_TOKEN` | `Authorization: Bearer ...` | Yes |
| `IMAGE_API_MODEL` | `model` | Yes |
| `IMAGE_API_SIZE` | `size` | No |
| `IMAGE_API_QUALITY` | `quality` | No |
| `IMAGE_API_OUTPUT_FORMAT` | `output_format` | No |
| `IMAGE_API_RESPONSE_FORMAT` | `response_format` | No |
| `IMAGE_API_TRANSPORT` | `curl` or `urllib` | No |
| `IMAGE_API_CONNECT_TIMEOUT_SECONDS` | Connection timeout | No |
| `IMAGE_API_TOTAL_TIMEOUT_SECONDS` | Complete request timeout | No |
| `IMAGE_API_PROXY_MODE` | `direct` or inherited `environment` proxy | No |
| `IMAGE_API_IMAGE_FIELD` | Multipart image field | No |
| `IMAGE_API_MULTI_IMAGE_MODE` | `repeat` or `indexed` fields | No |
| `IMAGE_API_EXTRA_FIELDS_JSON` | Extra multipart fields | No |

Resolve the provider without exposing the token:

```bash
python scripts/image_edit_provider.py resolve-provider --env provider.env --request "<current user request or provider preference>"
```

Strictly check a configured external API:

```bash
python scripts/image_edit_provider.py check-config --env provider.env
```

## Single-Image Endpoints

If the API accepts only one `image` field, merge the full reference and the numbered crop contact sheet first:

```bash
python scripts/image_edit_provider.py compose \
  --reference reference.png \
  --contact-sheet contact_sheets/batch_001.png \
  --out provider_inputs/batch_001.png
```

The generated composite labels the two panels. Tell the model to use the upper panel for global style and the lower panel for object identity and order.

## Smoke Tests

After intentionally changing provider configuration, optionally run two low-volume smoke tests before generating a full icon grid:

```bash
python scripts/run_provider_smoke_tests.py \
  --env provider.env \
  --image /absolute/path/to/test-image.png \
  --out-dir provider_test_outputs
```

The first test checks preservation plus a local edit. The second checks reference-conditioned blue line-icon generation on a chroma-key background. These smoke tests are for new or changed providers, not every skill run. For routine availability, use `scripts/provider_minimal_test.py`; it reuses `provider_test_record.json` when the provider fingerprint is unchanged and only calls the API when the record is missing or changed. Each external API call is retried up to 3 total attempts. If the tests still fail, stop and ask the user whether to use `local-draw` mode; do not silently fall back.

## Generate One Batch

```bash
python scripts/image_edit_provider.py generate-row \
  --env provider.env \
  --prompts prompts/assets.jsonl \
  --batch-id batch_001 \
  --image provider_inputs/batch_001.png \
  --out generated/batch_001.png
```

`generate-row` reads the matching `batch_id` from `prompts/assets.jsonl`, calls the image edit API with that prompt, accepts URL or Base64 image responses, validates the returned bytes as an image, and never includes the token in its output record. The call is retried up to 3 total attempts. If it still fails, stop before cutting assets or building the PPT and ask whether to switch to `local-draw`.

## Troubleshooting

- HTTP 401/403: verify token and model access; do not print the token.
- HTTP 400: compare model-supported `size`, `quality`, and field names with provider documentation.
- HTTP 5xx, timeouts, invalid JSON, missing image data, or invalid returned image: the adapter already retries up to 3 total attempts. After the third failure, stop and ask whether to use `local-draw`.
- Missing image in JSON: record one redacted response example and extend the response-key parser.
- Multiple images rejected: keep `IMAGE_API_MULTI_IMAGE_MODE=repeat` unused and upload one composed provider input.
