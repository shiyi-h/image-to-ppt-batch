---
name: image-to-ppt-batch
description: Use when recreating an editable PowerPoint deck from a slide image or infographic, especially when it contains many independently selectable icons, pictograms, logos, mini charts, or semantic visuals.
---

# Image To PPT Batch

## Overview

Rebuild a flat slide image as an editable PowerPoint from minimum semantic units. Keep text, layout, panels, arrows, dividers, and simple structural geometry native to PowerPoint. Generate or draw one transparent asset for each distinct semantic visual design, then reuse that file for visually identical placement instances.

## Asset Mode

Before inventory or asset work, select and announce the asset mode. Asset mode is a hard gate, not an implementation preference.

| Mode | Use when the user says | Default |
|---|---|---|
| `model-imagegen` | 模型生图, imagegen, AI生成, 用模型生成, 生成透明图标, or gives no explicit mode | Yes |
| `local-draw` | 本地绘制, 程序绘制, Pillow/SVG/代码画, 不用模型, 不调用imagegen, 离线, 确定性绘制 | No |

Default to `model-imagegen` unless the user has explicitly approved `local-draw` in the current request or in response to your question.

If you want to use `local-draw` and the user has not already explicitly approved it, stop before inventory or asset creation:

1. State the concrete reason you believe `local-draw` is needed.
2. Ask whether to use `local-draw`.
3. Use `local-draw` only if the user answers yes or otherwise clearly approves.
4. If the user says no, is ambiguous, or does not answer, use `model-imagegen`.

Do not choose `local-draw` merely because icons look simple, local drawing seems more reliable, the API may be slower, generated icons may vary, or the page contains UI icons.

Always record the selected mode in `mode_decision.json`, `asset_manifest.json`, and `validation_report.json`:

- `mode_decision.json` for default `model-imagegen`:

```json
{
  "asset_mode": "model-imagegen",
  "trigger": "default_no_local_draw_approval",
  "local_draw_reason": null,
  "user_approval": null
}
```

- `mode_decision.json` for `local-draw`:

```json
{
  "asset_mode": "local-draw",
  "trigger": "explicit_user_approval",
  "local_draw_reason": "<reason stated before asking>",
  "user_approval": "<exact user approval text>"
}
```

- `model-imagegen`: `source_type: "imagegen_chroma_key_cut"`
- `local-draw`: `source_type: "locally_drawn_semantic_asset"`

## Image Provider

For `model-imagegen`, select the provider by resolving the current user request and `provider.env` in this skill directory:

```bash
python scripts/image_edit_provider.py resolve-provider --env provider.env --request "<current user request or provider preference>"
```

- Explicit user provider instructions override `provider.env`.
- If the current request says `imageGen`, `Codex imagegen`, `内置 imagegen`, `默认 imagegen`, `不用 API`, `不要 API`, or `不调用外部接口`, use the built-in Codex `imagegen` tool even when `provider.env` is fully configured.
- If `provider.env` is absent, use the built-in Codex `imagegen` tool.
- If any of `IMAGE_API_ENDPOINT`, `IMAGE_API_TOKEN`, or `IMAGE_API_MODEL` is missing or blank in `provider.env`, use the built-in Codex `imagegen` tool.
- If all three are configured, use the external image edit API through `scripts/image_edit_provider.py`.
- Never print, paste, summarize, or expose `IMAGE_API_TOKEN`.
- If the three required fields exist but another provider setting is malformed, stop and report the configuration error instead of silently falling back.

Record the provider decision as `asset_manifest.provider` and `validation_report.provider`.

Read [external-image-api.md](references/external-image-api.md) only when `resolve-provider` returns `external-image-api`.

To minimally test a user-provided external provider, run the cached low-volume availability check:

```bash
python scripts/provider_minimal_test.py --env provider.env --out-dir provider_minimal_test
```

The script stores `provider_test_record.json` beside `provider.env`. The record contains the provider summary, a configuration fingerprint, and the last test result, never the token. On later runs, if the fingerprint still matches the current `provider.env` and relevant environment overrides, the script writes a cached result into the current `--out-dir` and does not call the API. It calls the API only when the record is missing, unreadable, or the provider fingerprint changed. The test only verifies that the provider config is valid, one edit request succeeds, and the returned bytes open as an image. It is not a visual-quality test. Never print or expose `IMAGE_API_TOKEN`.

External API calls are retried up to 3 total attempts by `scripts/image_edit_provider.py`. If the minimal provider test or any `external-image-api` generation call still fails after those attempts, stop the workflow immediately. Report the redacted failure and ask the user whether to use `local-draw` mode. Do not silently fall back to `local-draw`, do not continue with placeholder assets, and do not choose another provider unless the user explicitly instructs it. If the user approves `local-draw`, write `mode_decision.json` with `asset_mode: "local-draw"` and the exact approval text before creating any local assets.

## Non-Negotiable Rules

- One distinct icon, logo, pictogram, chart icon, or device design equals one final image file. Every occurrence on the slide still equals one independent PPT picture object.
- Use PPT-native objects only for text, backgrounds, panels, frames, dividers, connectors, and structural arrows.
- Never place the reference slide or a multi-icon grid in the final PPTX.
- Every generated-asset request must receive the full reference and a crop contact sheet. Text-only prompting is insufficient.
- In `local-draw` mode, generate transparent RGBA icon assets locally from minimum semantic geometry. Do not crop icons from the reference as final assets unless the user explicitly asks for raster crops.
- Use uniform contain scaling. Never stretch an image on one axis.

## Asset Reuse

Treat `visual_inventory.json.visuals` as placement instances. When multiple placements are visually identical, keep the first as the canonical asset and set `reuse_of` on every duplicate:

```json
[
  {"id": "value_check_1", "bbox": [174, 963, 26, 27], "label": "blue outlined circle with check mark", "style_key": "blue-check", "sensitivity": "simple"},
  {"id": "value_check_2", "bbox": [506, 963, 27, 27], "label": "blue outlined circle with check mark", "style_key": "blue-check", "sensitivity": "simple", "reuse_of": "value_check_1"}
]
```

Reuse only when silhouette, orientation, color, stroke, fill, internal text, and visual state are identical. Placement size may differ because PPT contain-scaling handles size. Do not reuse rotated, mirrored, recolored, highlighted, disabled, numbered, text-bearing, or otherwise state-different visuals.

Point `reuse_of` directly to a canonical visual ID. The planner validates missing targets and cycles, generates only canonical assets, and writes every placement to `batch_plan.json.placements` with `asset_id` and `asset_file`. During PPT assembly, insert the same canonical file separately at every mapped placement so each occurrence remains independently selectable.

Inventories without `reuse_of` retain the previous one-asset-per-item behavior.

## Batch Policy

For `model-imagegen`, use a dense-first policy for transparent icon generation. Pack as many simple items as practical into one request before splitting.

| Cohort | Examples | Maximum | Default action |
|---|---|---:|---|
| `simple` | Compact line icons, solid glyphs, simple pictograms, including mixed-color UI icon sets | 36 | One dense grid, up to 6x6 |
| `sensitive` | Logos, wordmarks, multicolor art, wide assets, fine detail | 4 | Separate or small grid |

Group by sensitivity first. Dense-pack all `simple` visuals together up to 36 items, even when their `style_key` or palette differs. Use `style_key` as descriptive metadata for prompts and QA, not as an automatic split key. Split simple visuals only when they need materially different rendering instructions, when the inventory explicitly sets `batch_key`, or when `--group-simple-by-style` is required after QA failures.

Read [inventory-schema.md](references/inventory-schema.md) before creating the visual inventory.

## Workflow

1. Create one output root and copy the input image to `reference.png`.
2. Complete the Asset Mode gate and write `mode_decision.json`. Default to `model-imagegen` unless the user explicitly approved `local-draw`.
3. If mode is `model-imagegen`, run `resolve-provider` and record the provider decision.
4. Inventory all text, layout objects, and minimum semantic visual placements. Give each placement a unique `id`, red-box `bbox`, `style_key`, and `sensitivity`. Mark exact duplicates with `reuse_of`; keep visually different states separate. Add `batch_key` only when a canonical simple visual must be separated from the default dense batch.
5. For `model-imagegen`, plan batches and create numbered crop contact sheets:

```bash
python scripts/batch_icons.py plan --inventory visual_inventory.json --reference reference.png --out batch_plan.json
python scripts/batch_icons.py contact-sheets --reference reference.png --plan batch_plan.json --out-dir contact_sheets
python scripts/batch_icons.py prompts --reference reference.png --plan batch_plan.json --out prompts/assets.jsonl
```

`batch_plan.json.batches` contains canonical generation assets only. `batch_plan.json.placements` contains every slide occurrence and maps it to `asset_id` and `asset_file`. Record `counts.unique_assets`, `counts.placement_instances`, and `counts.reused_instances` in QA.

6. Generate each batch grid:

- `codex-imagegen`: call the built-in imagegen tool once per JSONL row. Pass exactly two referenced images: `reference.png` and that row's contact sheet. Copy the result to the row's `generated_grid`.
- `external-image-api`: compose one provider input per batch, then call the adapter:

```bash
python scripts/image_edit_provider.py compose --reference reference.png --contact-sheet contact_sheets/batch_001.png --out provider_inputs/batch_001.png
python scripts/image_edit_provider.py generate-row --env provider.env --prompts prompts/assets.jsonl --batch-id batch_001 --image provider_inputs/batch_001.png --out generated/batch_001.png
```

`generate-row` retries the external API up to 3 total attempts. If it exits nonzero after those attempts, stop and ask the user whether to switch to `local-draw`; do not continue to cutting, validation, or PPT assembly.

7. Cut and validate each generated grid. Cutting creates one file per canonical asset, not one file per placement:

```bash
python scripts/batch_icons.py cut --plan batch_plan.json --batch-id batch_001 --grid generated/batch_001.png --out-dir assets --manifest-out generated/batch_001-cut.json
python scripts/batch_icons.py validate --manifest generated/batch_001-cut.json --out generated/batch_001-validation.json
```

8. If validation fails, split only that batch, regenerate its contact sheets and prompts, then retry:

```bash
python scripts/batch_icons.py split-batch --plan batch_plan.json --batch-id batch_001
```

9. For `local-draw`, create one transparent RGBA PNG per distinct visual design in `assets/`. Reuse canonical files for duplicate placements. Preserve the reference style, color, stroke weight, and silhouette. Keep readable labels as PPT-native text unless the visual is an identity-critical logo that the user explicitly wants as an image.
10. Match assets to every placement red box and run `subtract_assets.py` against all inventory placements, including reused ones. Inspect the residual at full size. Red-box any remaining semantic visual and repeat only for unresolved objects.
11. Build the PPTX with `build_pptx.py`. For each placement, insert a separate picture object using its mapped canonical `asset_file`; multiple picture objects may share the same file. Preserve explicit text line counts; use `balance_text_lines.py` and `align_from_redboxes.py` when groups share edges, centers, or gaps.
12. Render the final slide, compare it beside the reference, fix visible wrapping or alignment defects, and write `validation_report.json`.

## Failure Handling

### Provider

- Missing `provider.env`: use Codex imagegen.
- Missing `IMAGE_API_ENDPOINT`, `IMAGE_API_TOKEN`, or `IMAGE_API_MODEL`: use Codex imagegen.
- To check whether a user-provided external provider is basically usable, run `scripts/provider_minimal_test.py`. It reuses `provider_test_record.json` when the provider fingerprint is unchanged and uses the same 3-attempt external API retry behavior only when it actually tests.
- If an external provider request fails after 3 attempts, stop and ask the user whether to use `local-draw` mode. Record the response; only proceed with `local-draw` after explicit approval.
- HTTP 401/403 from the external API: verify token/model access without printing the token.
- HTTP 400 from the external API: compare size, quality, response format, and multipart field names against provider docs.
- Hanging requests: prefer `IMAGE_API_TRANSPORT=curl`, `IMAGE_API_PROXY_MODE=direct`, and explicit connect/total timeouts.

### Generated Assets

- `edge_touch`: the model crossed a cell boundary or drew cell borders. Split that batch; do not hand-trim a cut object.
- `empty_or_too_small`: verify contact-sheet crop and order, then regenerate the failed batch.
- Wrong style or object order: strengthen the object list and contact sheet; preserve the same batch geometry.
- Logo text corruption: move the logo to a single-item sensitive batch.
- More than 36 simple icons: let the planner create multiple grids.
- If a green or blue icon loses its fill during transparency cutting, regenerate or re-plan with a contrasting chroma key. The planner default is magenta `#FF00FF`.
- Incorrect reuse: remove `reuse_of` whenever color, orientation, stroke, text, state, or detail differs.
- Poor canonical crop: make the clearest occurrence canonical and point the other identical placements to it.

## Required Artifacts

Always keep `reference.png`, `mode_decision.json`, `visual_inventory.json`, `assets/`, residual images and red boxes, `asset_manifest.json`, `layout_rules.json`, the final PPTX, renders, and `validation_report.json`.

When reuse exists, record `unique_assets`, `placement_instances`, `reused_instances`, and each `{id, asset_id, asset_file, bbox}` mapping in `asset_manifest.json`. Keep `batch_plan.json.placements` as the machine-readable source mapping.

For `model-imagegen`, also keep `batch_plan.json`, `contact_sheets/`, `prompts/`, generated grids, cut manifests, validation JSON, and the provider decision JSON. For external API runs, also keep `provider_inputs/` and redacted API result records.

For `local-draw`, also keep the drawing source script(s), contact sheets used for QA, and a local asset preview sheet.

## Completion Gate

Deliver only after the PPTX renders successfully, the provider decision and asset mode are recorded, every semantic visual occurrence is an independent picture object, all text remains editable, no unresolved red boxes remain, and no text is clipped or accidentally wrapped. Reused occurrences may share one source PNG but must remain separate PPT objects.

Completion fails if `local-draw` is used without `mode_decision.json.user_approval`, or if `local-draw` was chosen for convenience rather than explicit user approval.
