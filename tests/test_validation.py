import unittest

from pipeline import snapshot


def gp_record(**overrides):
    record = {
        "NORAD_CAT_ID": "1",
        "CREATION_DATE": "2026-04-12T05:00:00",
        "EPOCH": "2026-04-12T04:00:00",
        "MEAN_MOTION": "15.0",
        "ECCENTRICITY": "0.0001",
        "INCLINATION": "51.6",
        "RA_OF_ASC_NODE": "10.0",
        "ARG_OF_PERICENTER": "20.0",
        "MEAN_ANOMALY": "30.0",
    }
    record.update(overrides)
    return record


class ValidationTests(unittest.TestCase):
    def test_rejects_dict_error_response(self):
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.validate_gp_records({"error": "bad request"}, min_count=0)

    def test_rejects_empty_or_too_small_lists(self):
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.validate_gp_records([], min_count=1)
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.validate_gp_records([gp_record()], min_count=2)

    def test_rejects_missing_required_fields(self):
        record = gp_record()
        del record["MEAN_MOTION"]

        with self.assertRaises(snapshot.SnapshotError):
            snapshot.validate_gp_records([record], min_count=1)

    def test_accepts_valid_records(self):
        snapshot.validate_gp_records([gp_record()], min_count=1)


if __name__ == "__main__":
    unittest.main()
