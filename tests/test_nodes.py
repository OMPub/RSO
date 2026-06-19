"""The backing-node roster the card extends its fall-through index rank with.

`build-nodes` publishes indexer/generated/nodes.json on the idx branch (beside the doc-chain
index). The card seeds its own ranked defaults and appends any roster node it doesn't already
know, so a new mirror joins the chain without re-minting the card.
"""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline import snapshot


class NodeRosterTests(unittest.TestCase):
    def test_roster_shape_matches_the_cards_node_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nodes.json"
            roster = snapshot.write_node_roster(out)
            self.assertEqual(roster["schema"], "rso-nodes-v1")
            self.assertIn("generated_at_utc", roster)
            self.assertTrue(out.exists())
            written = json.loads(out.read_text())
            self.assertEqual(written["nodes"], roster["nodes"])
            # every node carries exactly the keys the card's node objects use
            for node in roster["nodes"]:
                self.assertEqual(set(node), {"id", "label", "repo", "node", "idx"})

    def test_default_roster_leads_with_ompub(self):
        with tempfile.TemporaryDirectory() as tmp:
            roster = snapshot.write_node_roster(Path(tmp) / "nodes.json")
            ids = [n["id"] for n in roster["nodes"]]
            self.assertEqual(ids[0], "ompub")
            self.assertIn("brookr", ids)

    def test_published_roster_is_in_sync_with_the_default(self):
        # the committed indexer/generated/nodes.json must match what build-nodes emits, minus
        # the timestamp, so the published roster never drifts from NODE_ROSTER
        published = json.loads(snapshot.NODE_ROSTER_PATH.read_text())
        self.assertEqual(published["schema"], "rso-nodes-v1")
        self.assertEqual(published["nodes"], [dict(n) for n in snapshot.NODE_ROSTER])


if __name__ == "__main__":
    unittest.main()
