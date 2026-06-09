# RSO Doc Chain v2: Consensus Core and Observation Log

v2 is the active RSO attestation chain. It exists because of a measured fact
about the source data, and it is built around one principle:

> **Consensus covers only what is reproducible. Observations are recorded per
> node, with the time we learned them.**

## Why v1 was superseded

v1 hashed every field of every catalog record into the daily contentHash. A
field-by-field re-query of all 50 archived windows (2026-06-09) measured what
that assumed away:

- Space-Track **mutates object-directory fields in place** on already-published
  `gp_history` rows. Across 1,371,193 record-observations, 152 records changed
  (0.011%) — every one an in-place field mutation on the same `GP_ID`, never a
  selection change.
- Only the object-directory family mutates: `DECAY_DATE` (118 stamps, up to
  **7,224 days** after the fact), and the naming triplet
  `OBJECT_NAME`/`OBJECT_TYPE`/`TLE_LINE0` (35 each, TBA → assigned).
- Result: **0 of 50** archived v1 day hashes could be reproduced from a fresh
  query. With the mutable fields excluded, **50 of 50** reproduced exactly.
- No settling delay fixes this: decay stamps have no time horizon.
- Worse, the gp window capture alone is nearly blind to decay knowledge: of
  118 decay stamps in the coverage window, the archive caught **2** (decayed
  objects publish no further elsets, so the stamp lands on a row no later
  window revisits).

The two nodes' 2026-06-08 captures forked on exactly this mechanism (three
`DECAY_DATE` back-patches between their query times). Their raw hashes differ;
their v2 core hashes are identical.

## The field partition

Every raw record keeps all 39 OMM fields exactly as the API returned them.
The partition only affects what is hashed:

- **Core (30 fields, hashed):** elset identity and orbital state — `GP_ID`,
  `NORAD_CAT_ID`, `CREATION_DATE`, `EPOCH`, mean elements, drag terms,
  `TLE_LINE1`, `TLE_LINE2`, format/provenance fields. Measured immutable once
  published.
- **Observation (9 fields, excluded):** the object-directory family —
  `COUNTRY_CODE`, `DECAY_DATE`, `LAUNCH_DATE`, `OBJECT_ID`, `OBJECT_NAME`,
  `OBJECT_TYPE`, `RCS_SIZE`, `SITE`, `TLE_LINE0`. Excluded by *mechanism*
  (Space-Track back-fills directory attributes), not just by observed incident,
  so the next back-patched field is already outside consensus.

```text
content_sha256 = SHA-256( canonical JSON of records minus the 9 excluded fields )
```

The raw catalog hash (`sha256`) stays in the manifest as artifact integrity;
the chain attests `content_sha256`.

## The observation plane

Each day also publishes `annotations.json` — what this node learned about the
mutable fields, with the time of recording:

- `catalog_changes`: per-object diffs of the 9 fields between consecutive raw
  catalogs (`previous` → `current`, `first_observation` for new objects),
  stamped with `observed_at_utc`.
- `satcat_changes`: Space-Track's own catalog change log for the window
  (`satcat_change`, windowed on `CHANGE_MADE`) — previous→current transitions
  with Space-Track's own timestamps.
- `decay_messages`: the `decay` class for the window (windowed on
  `MSG_EPOCH`). This closes the decay-knowledge gap: reentries reach the
  archive the next day even though the decayed object publishes no elsets.

Annotations are per-node and eventually consistent — two honest nodes may hold
different observations for the same day. They are signed into each node's
publication locator (the attestation `uri`), so they are chain-committed
per-node without perturbing blockHash agreement. `manifest.annotations_sha256`
fingerprints the artifact.

Historical days (rebuilt 2026-06-09) carry `rebuilt: true` and use each day's
original `archived_at` as `observed_at_utc`: nothing was fabricated; the raw
catalogs are the observations.

## Chain identity and supersession

- Profile URI: `https://om.pub/rso/doc-chain/v2`
- `docChainId = keccak256(profile URI)` =
  `0x7c5d6ad47ba584ce3f34ec8f94b08d17d4828c1d5ee6fbaecb4dfcb986efbc40`
- Contract: DocChain release 2 (adds `attestBatch`), Sepolia
  `0x2c66585E7b60A20563a3fd2B7a4D75Ae5baa5437`
- Genesis: docRef `20260420000000`, whose `parentHash` is the **agreed v1 head
  block** (`0xf6b2b68e...d92ca4`, the 2026-06-07 block both nodes attested) —
  the supersession is recorded on chain.
- v1 (profile `.../v1`, contract `0x1133895b...92F5DA`) is frozen and fully
  preserved: its on-chain history, release assets, and `storage-v1.json`
  receipts are never mutated.

## Verifying the chain from nothing

Any party can re-derive every v2 contentHash from the source:

1. Fetch the genesis raw catalog (anchor artifact; its bytes are attested and
   published).
2. For each day, query `gp_history` for `CREATION_DATE` in
   `[D-1 00:00Z, D 00:00Z]`, apply the published selection rule
   (`filter_creation_window` + `dedupe_latest_per_object`), and fold into the
   prior state.
3. Project out the 9 excluded fields, canonicalize, hash.

This was executed against the full chain on 2026-06-09: 51/51 days reproduced
from a fresh capture, including the day the raw captures forked.

## The guardrail

The weekly **drift audit** (`pipeline/drift_audit.py`) re-queries a sliding
sample of archived windows and classifies every difference. Excluded-field
drift is expected and logged. Any non-excluded-field mutation or selection
drift — the signals that would threaten consensus — fails the run and opens a
repository issue. The partition is an empirical claim; the audit is its
continuous test.
