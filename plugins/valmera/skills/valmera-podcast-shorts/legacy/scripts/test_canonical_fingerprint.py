#!/usr/bin/env python3

from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).with_name("canonical_fingerprint.py")
SPEC = importlib.util.spec_from_file_location("canonical_fingerprint", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class CanonicalFingerprintTests(unittest.TestCase):
    def test_key_order_and_equivalent_numbers_match(self):
        left = {"b": Decimal("1.0"), "a": [Decimal("2.500"), "é"]}
        right = {"a": [Decimal("2.5"), "é"], "b": Decimal("1")}
        self.assertEqual(MODULE.fingerprint("selection", left),
                         MODULE.fingerprint("selection", right))

    def test_selection_self_field_is_excluded(self):
        base = {"clips": [{"start": Decimal("1.2")}],
                "selection_fingerprint": "sha256:old"}
        changed = {**base, "selection_fingerprint": "sha256:new"}
        self.assertEqual(MODULE.fingerprint("selection", base),
                         MODULE.fingerprint("selection", changed))

    def test_assignment_excludes_only_self_fingerprint(self):
        base = {"assignment_id": "a", "assignment_input_fingerprint": "sha256:old",
                "treatment": {"music_policy": "none"}}
        new_fingerprint = {**base, "assignment_input_fingerprint": "sha256:new"}
        injected_lease = {**base, "valmera_lease_id": "forbidden-assignment-lease"}
        new_treatment = {**base, "treatment": {"music_policy": "subtle_bed"}}
        self.assertEqual(MODULE.fingerprint("assignment", base),
                         MODULE.fingerprint("assignment", new_fingerprint))
        self.assertNotEqual(MODULE.fingerprint("assignment", base),
                            MODULE.fingerprint("assignment", injected_lease))
        self.assertNotEqual(MODULE.fingerprint("assignment", base),
                            MODULE.fingerprint("assignment", new_treatment))

    def test_negative_zero_normalizes(self):
        self.assertEqual(MODULE.canonical_json(Decimal("-0.000")), "0")


if __name__ == "__main__":
    unittest.main()
