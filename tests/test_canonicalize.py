import hashlib
import unittest

from pipeline import snapshot


class CanonicalizeTests(unittest.TestCase):
    def test_canonical_json_is_stable_and_hashed(self):
        data = [{"b": 2, "a": 1}]
        canonical = snapshot.canonicalize(data)

        self.assertEqual(canonical, b'[{"a":1,"b":2}]')
        self.assertEqual(
            snapshot.compute_hash(canonical),
            "44c7deead2ed8313d29655e45c0d1469419213c93d9f44d66da7c7afe46e74e3",
        )
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), snapshot.compute_hash(canonical))

    def test_key_order_and_whitespace_do_not_change_output(self):
        left = [{"z": 3, "a": {"y": 2, "x": 1}}]
        right = [{"a": {"x": 1, "y": 2}, "z": 3}]

        self.assertEqual(snapshot.canonicalize(left), snapshot.canonicalize(right))
        self.assertNotIn(b" ", snapshot.canonicalize(left))


if __name__ == "__main__":
    unittest.main()
