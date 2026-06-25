"""Unit tests for the doppelganger runner's pure helpers (no GPU/network/IO).

    python -m unittest discover -s tests -t .
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doppelganger import steps


class EffectiveBatchTest(unittest.TestCase):
    def test_multiplies_batch_accum_gpus(self):
        cfg = {"per_device_train_batch_size": 2, "gradient_accumulation_steps": 8}
        self.assertEqual(steps.effective_batch(cfg, num_gpus=1), 16)
        self.assertEqual(steps.effective_batch(cfg, num_gpus=4), 64)

    def test_defaults_when_missing(self):
        self.assertEqual(steps.effective_batch({}, num_gpus=1), 1)


class RecommendEpochsTest(unittest.TestCase):
    def test_healthy_midsize_dataset_one_epoch(self):
        # ~3.3k samples, eff batch 16 -> ~208 steps/epoch -> 1 epoch.
        epochs, spe, warnings = steps.recommend_epochs(3324, 16)
        self.assertEqual(epochs, 1)
        self.assertEqual(spe, 208)
        self.assertEqual(warnings, [])

    def test_small_dataset_is_capped_and_warned(self):
        epochs, _, warnings = steps.recommend_epochs(997, 16)
        self.assertLessEqual(epochs, 2)          # capped, not cranked to hit step budget
        self.assertTrue(any("small dataset" in w for w in warnings))

    def test_large_dataset_one_epoch(self):
        epochs, _, warnings = steps.recommend_epochs(20000, 16)
        self.assertEqual(epochs, 1)
        self.assertEqual(warnings, [])

    def test_never_exceeds_cap(self):
        for n in (1200, 1500, 2500, 5000):
            epochs, _, _ = steps.recommend_epochs(n, 16)
            self.assertGreaterEqual(epochs, 1)
            self.assertLessEqual(epochs, steps._MAX_EPOCHS)

    def test_no_dataset_warns(self):
        _, _, warnings = steps.recommend_epochs(0, 16)
        self.assertTrue(warnings)


class SummaryTest(unittest.TestCase):
    """Post-training visibility helpers — best-effort, never raise."""

    def test_loss_summary_formats_trend(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "trainer_log.jsonl")
            with open(p, "w") as f:
                for s, loss in [(10, 8.0), (20, 4.0), (30, 3.0)]:
                    f.write(json.dumps({"current_steps": s, "loss": loss}) + "\n")
            out = steps._loss_summary(p)
            self.assertIn("8.00", out)
            self.assertIn("3.00", out)        # final
            self.assertIn("min 3.00", out)

    def test_loss_summary_missing_file(self):
        self.assertIsNone(steps._loss_summary("/no/such/log.jsonl"))

    def test_summarize_run_missing_dir_is_silent(self):
        # Must not raise even when nothing exists.
        steps.summarize_run("/no/such/output_dir")


if __name__ == "__main__":
    unittest.main()
