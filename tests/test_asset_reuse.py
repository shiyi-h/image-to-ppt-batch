from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import batch_icons


def visual(
    visual_id: str,
    bbox: list[int],
    *,
    label: str = "blue outlined circle with check mark",
    reuse_of: str | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": visual_id,
        "label": label,
        "bbox": bbox,
        "style_key": "blue-check",
        "sensitivity": "simple",
    }
    if reuse_of is not None:
        item["reuse_of"] = reuse_of
    return item


class AssetReuseTest(unittest.TestCase):
    def test_plan_generates_one_asset_for_four_reused_check_placements(self) -> None:
        items = [
            visual("check_1", [10, 10, 24, 24]),
            visual("check_2", [50, 10, 24, 24], reuse_of="check_1"),
            visual("check_3", [90, 10, 24, 24], reuse_of="check_1"),
            visual("check_4", [130, 10, 24, 24], reuse_of="check_1"),
            visual(
                "trend",
                [200, 10, 40, 40],
                label="blue rising trend chart",
            ),
        ]

        plan = batch_icons.build_plan_document(items, Path("reference.png"))

        generated_ids = [
            item["id"]
            for batch in plan["batches"]
            for item in batch["items"]
        ]
        self.assertEqual(generated_ids, ["check_1", "trend"])
        self.assertEqual(plan["counts"]["unique_assets"], 2)
        self.assertEqual(plan["counts"]["placement_instances"], 5)
        self.assertEqual(plan["counts"]["reused_instances"], 3)
        self.assertEqual(
            [placement["asset_id"] for placement in plan["placements"]],
            ["check_1", "check_1", "check_1", "check_1", "trend"],
        )
        self.assertEqual(plan["placements"][2]["asset_file"], "check_1.png")
        self.assertEqual(plan["placements"][2]["bbox"], [90, 10, 24, 24])

    def test_inventory_without_reuse_keeps_previous_one_asset_per_item_behavior(self) -> None:
        items = [
            visual("check_1", [10, 10, 24, 24]),
            visual("trend", [50, 10, 40, 40], label="blue rising trend chart"),
        ]

        assets, placements = batch_icons.resolve_reuse(items)

        self.assertEqual([item["id"] for item in assets], ["check_1", "trend"])
        self.assertEqual(
            [placement["asset_id"] for placement in placements],
            ["check_1", "trend"],
        )

    def test_missing_reuse_target_is_rejected(self) -> None:
        items = [
            visual("check_2", [50, 10, 24, 24], reuse_of="check_missing"),
        ]

        with self.assertRaisesRegex(ValueError, "reuse target not found"):
            batch_icons.resolve_reuse(items)

    def test_reuse_cycle_is_rejected(self) -> None:
        items = [
            visual("check_1", [10, 10, 24, 24], reuse_of="check_2"),
            visual("check_2", [50, 10, 24, 24], reuse_of="check_1"),
        ]

        with self.assertRaisesRegex(ValueError, "reuse cycle"):
            batch_icons.resolve_reuse(items)

    def test_duplicate_visual_ids_are_rejected(self) -> None:
        items = [
            visual("check_1", [10, 10, 24, 24]),
            visual("check_1", [50, 10, 24, 24]),
        ]

        with self.assertRaisesRegex(ValueError, "duplicate visual id"):
            batch_icons.resolve_reuse(items)


if __name__ == "__main__":
    unittest.main()
