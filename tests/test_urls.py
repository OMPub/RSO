import urllib.request
import unittest

from pipeline import snapshot


class UrlTests(unittest.TestCase):
    def test_query_path_has_no_raw_spaces(self):
        path = snapshot.build_query_path(
            "gp_history",
            [
                ("NORAD_CAT_ID", "0--9999"),
                ("CREATION_DATE", "2026-04-12T00:00:00--2026-04-13T00:00:00"),
                ("orderby", "NORAD_CAT_ID asc,CREATION_DATE desc"),
            ],
        )

        self.assertNotIn(" ", path)
        self.assertIn("%20", path)
        self.assertIn("2026-04-12T00:00:00--2026-04-13T00:00:00", path)
        urllib.request.Request(f"{snapshot.SPACETRACK_QUERY}{path}")

    def test_all_snapshot_query_paths_are_url_safe(self):
        path = snapshot.build_query_path(
            "gp_history",
            [
                ("NORAD_CAT_ID", "10000--19999"),
                ("CREATION_DATE", "2026-04-12T00:00:00--2026-04-13T00:00:00"),
                ("orderby", "NORAD_CAT_ID asc,CREATION_DATE desc"),
            ],
        )
        snapshot.validate_query_url(f"{snapshot.SPACETRACK_QUERY}{path}")

    def test_raw_whitespace_is_rejected(self):
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.validate_query_url("https://www.space-track.org/a b")


if __name__ == "__main__":
    unittest.main()
