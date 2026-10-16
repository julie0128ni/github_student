import tempfile
import unittest
from pathlib import Path

from groupctl.config import GroupConfig
from groupctl.events import common_event, validate_event


class EventContractTests(unittest.TestCase):
    def cfg(self, root):
        return GroupConfig(root, "0.1", "G-1", "main", ".group/assignment/assignment.md")

    def test_common_envelope(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.cfg(Path(d))
            e = common_event(cfg, "P1", "finding")
            e.update({"summary": "x", "evidence": ["y"]})
            validate_event(e, cfg)
            self.assertEqual(e["protocol_version"], "0.1")
            self.assertEqual(e["group_id"], "G-1")
            self.assertEqual(e["person_id"], "P1")

    def test_review_verdict_is_checked(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.cfg(Path(d))
            e = common_event(cfg, "P1", "review")
            e.update({
                "change_id": "C-1", "verdict": "maybe", "summary": "x",
                "issues": [], "tests_run": []
            })
            with self.assertRaises(Exception):
                validate_event(e, cfg)


if __name__ == "__main__":
    unittest.main()
