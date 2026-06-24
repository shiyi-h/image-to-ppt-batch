# Visual Inventory Schema

## Minimum fields

```json
{
  "reference": "reference.png",
  "canvas": {"width": 1672, "height": 941},
  "visuals": [
    {
      "id": "college_data",
      "label": "blue outline stacked database icon",
      "bbox": [64, 326, 40, 40],
      "style_key": "china-mobile-blue-line",
      "sensitivity": "simple"
    },
    {
      "id": "brand_logo",
      "label": "China Mobile bilingual logo and wordmark",
      "bbox": [1444, 19, 214, 64],
      "style_key": "china-mobile-brand",
      "sensitivity": "sensitive",
      "allow_text": true
    }
  ]
}
```

`bbox` uses reference-image pixels in `[x, y, width, height]` order.

## Classification

- `simple`: one compact object, one dominant style, no readable text, no fine photographic detail.
- `sensitive`: logo or wordmark, readable text, multicolor identity art, unusually wide/tall object, small internal detail, or identity-critical graphic.
- `style_key`: descriptive metadata for prompting and QA. The default planner does not split `simple` visuals by `style_key`; it dense-packs simple icons together to reduce imagegen calls.
- `batch_key`: optional hard grouping key. Add it only when a visual needs separate rendering instructions or must not share a dense simple grid.

## Batch sizing

The planner searches grid shapes with at most 6 columns and 6 rows, keeps cells at least 160px by default, minimizes blank cells, then prefers near-square cells. Typical results at 1536x1024:

| Objects | Grid |
|---:|---:|
| 6 | 3x2 |
| 8 | 4x2 |
| 12 | 4x3 |
| 20 | 5x4 |
| 25-30 | 6x5 |
| 31-36 | 6x6 |

## Contact-sheet contract

- Match the declared output grid.
- Put one reference crop in each occupied cell, in output order.
- Keep the numbered ID outside the crop; it is mapping metadata, not an output label.
- Pass the contact sheet plus the full reference as the only two image inputs.

## Retry ladder

1. Validate the cut grid.
2. If one batch fails, run `split-batch` for that batch only.
3. Regenerate the two child contact sheets and prompts.
4. Stop splitting when a batch has one item; then revise its crop or prompt instead.
