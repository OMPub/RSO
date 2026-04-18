import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from pipeline import snapshot


def gp_record(cat_id="1"):
    return {
        "NORAD_CAT_ID": cat_id,
        "CREATION_DATE": "2026-04-18T05:00:00",
        "EPOCH": "2026-04-18T04:00:00",
        "MEAN_MOTION": "15.0",
        "ECCENTRICITY": "0.0001",
        "INCLINATION": "51.6",
        "RA_OF_ASC_NODE": "10.0",
        "ARG_OF_PERICENTER": "20.0",
        "MEAN_ANOMALY": "30.0",
    }


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original_data_dir = snapshot.DATA_DIR
        snapshot.DATA_DIR = self.root / "data"

    def tearDown(self):
        snapshot.DATA_DIR = self.original_data_dir
        self.tmp.cleanup()

    def archive_day(self, current_date_str="2026-04-18"):
        records = [gp_record("1"), gp_record("2")]
        data = sorted(records, key=snapshot.catalog_id_sort_key)
        return snapshot.save_snapshot(
            current_date_str,
            snapshot.canonicalize(data),
            data,
            "genesis_from_gp",
            "current_gp_genesis",
            ["/class/gp/orderby/NORAD_CAT_ID%20asc/format/json"],
            observed_at_utc=f"{current_date_str}T07:15:00Z",
            state_as_of_utc=f"{current_date_str}T00:00:00Z",
        )

    def test_release_bundle_is_deterministic(self):
        self.archive_day()

        first = snapshot.build_release_bundle(
            "2026-04-18", output_dir=self.root / "first", min_count=1
        )
        second = snapshot.build_release_bundle(
            "2026-04-18", output_dir=self.root / "second", min_count=1
        )

        self.assertEqual(first["bundle_sha256"], second["bundle_sha256"])
        self.assertEqual(
            Path(first["path"]).read_bytes(),
            Path(second["path"]).read_bytes(),
        )

    def test_release_bundle_contains_expected_files_and_manifest(self):
        manifest = self.archive_day()

        bundle = snapshot.build_release_bundle(
            "2026-04-18", output_dir=self.root / "bundle", min_count=1
        )

        with tarfile.open(bundle["path"], mode="r:gz") as tar:
            names = sorted(tar.getnames())
            self.assertEqual(names, ["catalog.json.gz", "manifest.json", "release-manifest.json"])
            release_manifest = json.load(tar.extractfile("release-manifest.json"))

        self.assertEqual(release_manifest["date"], "2026-04-18")
        self.assertEqual(release_manifest["catalog_sha256"], manifest["sha256"])
        self.assertEqual(release_manifest["object_count"], 2)
        self.assertEqual(snapshot.release_tag("2026-04-18"), "rso-archive-2026-04-18")
        self.assertEqual(
            snapshot.release_asset_name("2026-04-18"),
            "rso-archive-2026-04-18.tar.gz",
        )

    def test_github_release_publish_skips_existing_asset_without_force(self):
        calls = []
        original_assets = snapshot.github_release_assets
        original_run_gh = snapshot.run_gh
        try:
            snapshot.github_release_assets = lambda tag, repo=None: [
                "rso-archive-2026-04-18.tar.gz"
            ]
            snapshot.run_gh = lambda args, allow_failure=False: calls.append(args)
            bundle = {
                "tag": "rso-archive-2026-04-18",
                "asset_name": "rso-archive-2026-04-18.tar.gz",
            }

            result = snapshot.publish_github_release(
                bundle,
                upload_policy="always_mirror",
                force=False,
            )

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "asset_exists")
            self.assertEqual(calls, [])
        finally:
            snapshot.github_release_assets = original_assets
            snapshot.run_gh = original_run_gh


if __name__ == "__main__":
    unittest.main()
