"""Contract tests for the archive card viewer (card/index.html).

The card is a single-file artwork that consumes live repo artifacts. These
tests pin the contracts between the viewer and the rest of the pipeline so a
change on either side fails loudly:

- the viewer stays self-contained (vendored three.js, one inline module);
- it only fetches bundle mirrors a browser can actually read (Arweave + raw
  node-branch files — never GitHub release assets, whose redirect target sends
  no CORS headers);
- the generated attestation index keeps the shape the viewer parses;
- the witness gate defaults stay coherent between markup and script.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "card" / "index.html"
INDEX = ROOT / "indexer" / "generated" / "sepolia" / "rso-docchain-index.json"


class CardArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = CARD.read_text(encoding="utf-8")

    def test_single_file_with_vendored_three(self):
        self.assertTrue(CARD.is_file())
        self.assertEqual(self.html.count("<script"), 1, "one inline module only")
        self.assertIn('import(new URL("./three.module.js", MOUNT)', self.html)
        for vendored in ("three.module.js", "three.core.js"):
            self.assertTrue((CARD.parent / vendored).is_file(), vendored)
        # no CDN/runtime dependencies — the piece must outlive any host
        self.assertNotIn("cdn.jsdelivr.net", self.html)
        self.assertNotIn("unpkg.com", self.html)
        self.assertNotIn("esm.sh", self.html)

    def test_mount_agnostic_module_resolution(self):
        # served at a slashless path (om.pub/rso/live, Arweave gateways) a bare
        # relative import would resolve against the parent directory
        self.assertIn("const MOUNT = new URL(location.href)", self.html)
        self.assertIn('MOUNT.pathname += "/"', self.html)

    def test_bundle_mirrors_are_browser_readable(self):
        # Arweave first, then the raw node-branch catalog — and never the
        # GitHub release asset, which a browser can never read cross-origin.
        self.assertIn("dest.arweave?.transaction_url", self.html)
        self.assertIn("catalog.json.gz`", self.html)
        self.assertNotIn("releases/download", self.html)
        self.assertNotIn("asset_url", self.html)

    def test_observation_plane_is_wired(self):
        self.assertIn("annotations.json", self.html)
        self.assertIn("digestAnnotations", self.html)
        self.assertIn('"Observations"', self.html)
        # decay notices / namings / amendments each get a distinct colour
        for const in ("OBS_DECAY", "OBS_NAME", "OBS_EDIT"):
            self.assertIn(const, self.html)

    def test_witness_gate_defaults_are_coherent(self):
        # script default, persisted-settings fallback and slider markup must
        # agree: rank 0 = any sweeper-accepted witness counts
        self.assertRegex(self.html, r"ethRank:\s*0\b")
        self.assertRegex(self.html, r"s\.ethRank \?\? 0")
        slider = re.search(r'<input type="range" id="set-eth"[^>]*>', self.html)
        self.assertIsNotNone(slider)
        self.assertIn('value="0"', slider.group(0))

    def test_downloads_are_verified_on_device(self):
        # the ledger sha256 hashes the canonical catalog bytes — exactly what
        # the viewer holds after gunzip, so one digest proves the download is
        # the attested record
        self.assertIn('crypto.subtle.digest("SHA-256", bytes)', self.html)
        self.assertIn("verifyCatalogBytes(date, catBytes)", self.html)
        self.assertIn("hex === led.sha", self.html)
        self.assertIn("verified on this device", self.html)
        self.assertIn("DOES NOT MATCH DOWNLOAD", self.html)

    def test_attested_core_face_shows_consensus_hash(self):
        self.assertIn('id="fp-core"', self.html)
        self.assertIn("content_sha256", self.html)
        self.assertIn("content_schema", self.html)

    def test_contract_link_is_chain_aware(self):
        self.assertIn('id="fp-contract"', self.html)
        self.assertIn("sepolia.etherscan.io", self.html)
        self.assertIn("https://etherscan.io", self.html)
        # links inside a rotating prism must not also rotate it
        self.assertIn('closest("a")', self.html)

    def test_field_shows_whats_up_there(self):
        # re-entered objects keep their slot only as today's decay event or a
        # fresh observation; the long-gone never fly past the camera
        self.assertIn(
            '!o.reentered || o.status === "decayed" || o.anno', self.html
        )

    def test_per_type_silhouettes(self):
        self.assertIn("aShape", self.html)
        for marker in ("payload — disc", "rocket body — tilted pill",
                       "debris — angular shard", "unknown — hollow ring"):
            self.assertIn(marker, self.html)

    def test_overdrive_respects_reduced_motion(self):
        self.assertIn("__HOLD_OVER__", self.html)
        self.assertRegex(self.html, r"reduced \? 0\s*:\s*smooth\(")

    def test_lenses_end_with_zen(self):
        lenses = re.search(r"const LENSES = \[(.*?)\];", self.html)
        self.assertIsNotNone(lenses)
        names = re.findall(r'"([^"]+)"', lenses.group(1))
        self.assertEqual(names[-1], "Zen")
        self.assertIn("Observations", names)
        self.assertIn('LENSES.indexOf("Zen")', self.html)


class AttestationIndexContractTest(unittest.TestCase):
    """The exact fields the viewer (and om.pub pages) read from the index."""

    @classmethod
    def setUpClass(cls):
        cls.index = json.loads(INDEX.read_text(encoding="utf-8"))

    def test_chain_metadata(self):
        self.assertTrue(
            re.fullmatch(r"0x[0-9a-fA-F]{40}", self.index["contractAddress"])
        )
        self.assertIsInstance(self.index["chainId"], int)
        self.assertGreaterEqual(self.index["docRefCount"], 1)

    def test_docref_entries_carry_what_the_viewer_parses(self):
        refs = self.index["docRefs"]
        self.assertEqual(len(refs), self.index["docRefCount"])
        for ref, rec in refs.items():
            self.assertRegex(rec["date"], r"^\d{4}-\d{2}-\d{2}$", ref)
            groups = rec.get("agreementGroups")
            self.assertTrue(groups, f"{ref} has no agreement groups")
            for group in groups:
                self.assertIn("blockHash", group)
                self.assertIn("combinedSupportTdh", group)
                self.assertIsInstance(group.get("attesters"), list)
            self.assertTrue(rec.get("blockHashes"), ref)

    def test_dates_are_contiguous_daily(self):
        from datetime import date, timedelta

        dates = sorted(
            date.fromisoformat(rec["date"])
            for rec in self.index["docRefs"].values()
        )
        for previous, current in zip(dates, dates[1:]):
            self.assertEqual(
                current - previous, timedelta(days=1),
                f"gap between {previous} and {current}",
            )


if __name__ == "__main__":
    unittest.main()
