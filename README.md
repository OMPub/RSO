# Orbital Witness: RSO Archive

**Permanent, decentralized, tamper-evident archive of the public space object catalog.**

Orbital Witness is a pattern for public, independently operated, onchain-ready
archives of orbital datasets. 

This repository is the first witness: the daily
Resident Space Object (RSO) catalog. The same shape can later be reused for
near-Earth objects (NEO), conjunction events, launch records, reentry
observations, or any other public space dataset where provenance matters.

Every UTC day, the record is cut at **00:00:00 UTC**. The operator runs should
happen a few minutes or hours after the cutoff, leaving Space-Track several
hours to settle (and to be nice to the API).

---

## What this does

Builds a daily General Perturbations (GP) catalog snapshot from [Space-Track.org](https://www.space-track.org), computes a canonical SHA-256 hash, and stores the snapshot permanently.

The refined snapshot model is stateful: each daily catalog is derived from the
prior archived catalog plus a bounded `gp_history` delta window. This avoids
unbounded historical queries while keeping the snapshot deterministic for
operators who start from the same prior consensus snapshot.

The catalog currently tracks **50,000+** resident space objects (RSOs): active satellites, defunct spacecraft, rocket bodies, and debris.

## Why

The public space object catalog originates from a single source (U.S. Space Force, 18th Space Defense Squadron) and is mirrored by a single individual ([CelesTrak](https://celestrak.org)). There is no redundant historical archive with cryptographic proof of what the catalog said on any given date.

If data is retroactively altered, reclassified, or withdrawn — which happens — nobody can independently verify what changed. This project fixes that.

## Architecture

```
Dataset Source ──────────────→ Witness Pipeline ──→ SHA-256 Hash
     │                                  │
     ▼                                  ▼
Space-Track GP_HISTORY            RSO Archive
                         │                  │
                         ▼                  ▼
                    Arweave (permanent)  Ethereum (attestation)
                         │                  │
                         └────────┬─────────┘
                                  ▼
                         NFT Artwork (verification)
```

**Phase 1 (current):** Daily snapshots archived to Git with SHA-256 hashes.

**Phase 2:** Arweave permanent storage + Ethereum on-chain attestation.

**Phase 3:** Dynamic NFT artwork on 6529 The Memes that reads from Arweave and Ethereum, verifies data integrity client-side, and displays verification status. TDH-weighted community confirmations.

**Phase 4:** Orbital Witness expansion to other datasets: NEO, conjunction events, fireball observations, etc.

Future Orbital Witness archives should keep the same operator experience:
bounded source queries, canonical JSON, reproducible hashes, public manifests,
read-only validation, and eventually permanent storage plus onchain
attestation. RSO comes first because the catalog is public, important, and
fragile enough to justify the machinery.

## Archive Schedule

The official archive baseline is **2026-04-20**. On that day, the scheduled
workflow runs `genesis --date 2026-04-20` at 12:15am Pacific and records current
`gp` as the first agreed full catalog state. Daily consensus snapshots after
that are built from bounded `gp_history` deltas.

The existing `2026-04-13` genesis snapshot is a rehearsal baseline. It lets us
exercise daily roll-forward, audits, backfill behavior, and reporting during
the practice week without treating those artifacts as the permanent launch
point.

## Snapshot Specification

| Field | Value |
|-------|-------|
| Source | Space-Track.org GP_HISTORY class |
| Format | OMM/JSON |
| Snapshot cutoff | 00:00:00 UTC daily |
| Operator run time | 12:15am Pacific (`07:15 UTC` during daylight time) |
| Canonical source | Prior archived snapshot plus bounded `gp_history` delta |
| Sort | NORAD_CAT_ID ascending after merge |
| Hash | SHA-256 of canonical JSON (sorted keys, no whitespace) |
| Compression | gzip level 9 |
| Provenance | `genesis_from_gp` or `rolling_gp_history_delta` |

The snapshot cutoff is fixed at midnight UTC. A snapshot dated `2026-04-13`
represents the catalog state as of `2026-04-13T00:00:00Z`. The GitHub Action may
run later in the day, but the data window is already closed.

For daily operation, the pipeline starts from the previous archived snapshot and
queries only the bounded history interval:

```text
previous_cutoff <= CREATION_DATE < current_cutoff
```

For example, the `2026-04-13` snapshot uses:

```text
base:  snapshot at 2026-04-12T00:00:00Z
delta: gp_history CREATION_DATE/2026-04-12T00:00:00--2026-04-13T00:00:00
```

Within the delta, the pipeline selects the latest published row per
`NORAD_CAT_ID` by `CREATION_DATE`, then `GP_ID`, then `EPOCH`, and applies it to
the base snapshot only if it is newer than the stored row by that same ordering.
`CREATION_DATE` controls both whether a row is inside the bounded publication
window and which public row supersedes the previous archived row. Objects that do not appear in the
bounded delta are carried forward unchanged. Absence from a one-day
`gp_history` window is normal; it only means Space-Track did not publish a new
public element set for that object during that UTC day.

`CREATION_DATE` is the publication timestamp for a GP element set row. It is not
the launch date or object creation date. Existing objects receive new
`CREATION_DATE` values whenever Space-Track publishes updated public elements.

The canonical snapshot must not use current `gp` as an input to the daily merge,
because current `gp` is retrieval-time dependent and is not exactly
reconstructible from a simple public ordering over `gp_history` in all cases.
Current `gp` is useful as a genesis input and as an audit observation.

### Genesis Snapshot

The rolling model needs an agreed starting point. The first live day should be
captured as a `genesis_from_gp` snapshot from current `gp`, with the exact query
time and query paths recorded. From that point forward, daily snapshots are
deterministic state transitions:

```text
snapshot[D] = snapshot[D-1] + bounded_gp_history_delta[D]
```

Historical backfills before genesis can still be useful, but they should be
labeled as reconstructed history rather than treated as having the same
guarantee as the live rolling archive.

A genesis snapshot records `state_as_of_utc` as the actual current-`gp`
observation time. The first daily snapshot after genesis starts its bounded
`gp_history` window from that observed timestamp, then later snapshots use
normal midnight-to-midnight UTC windows.

### Visibility Audit

Removals and disappearances are intentionally not allowed to mutate the
canonical catalog unless a deterministic removal rule is later defined. Instead,
the pipeline stores observation-time audit artifacts beside the consensus
snapshot.

The daily audit should query current `gp` once at the official run time and can
also query bounded `satcat_change` / `satcat` metadata to explain decay or
catalog metadata changes. It records:

```text
observed_at_utc
query_path
current_gp_object_count
present_ids_sha256
missing_from_current_gp
reappeared_in_current_gp
```

Every presence or absence claim from current `gp` must include the audit
timestamp, because it is a time-sampled observation rather than a closed-window
fact.

The audit should also keep visibility state for currently missing objects:

```text
last_gp_creation_date
last_seen_in_current_gp_audit
first_missing_in_current_gp_audit
consecutive_missing_audits
satcat_decay
```

If an archived object is absent from current `gp` but has no `satcat` decay
date, the audit reports it as `missing_from_current_gp`. The object remains in
the canonical snapshot, making the disappearance visible without making the
hash depend on retrieval-time absence. If it later appears in current `gp`
again, the audit records a `reappeared_in_current_gp` event and preserves the
missing interval.

## Quick Start

### Prerequisites

- Python 3.10+
- Free [Space-Track.org](https://www.space-track.org/auth/createAccount) account

### Local Usage

```bash
# Set credentials
export SPACETRACK_USER="your@email.com"
export SPACETRACK_PASS="your-password"

# Capture the first agreed rolling snapshot from current gp
python pipeline/snapshot.py genesis

# Official baseline day, scheduled for 2026-04-20
python pipeline/snapshot.py genesis --date 2026-04-20

# Rehearsal baseline currently used during practice week
python pipeline/snapshot.py genesis --date 2026-04-13

# Build today's rolling snapshot from yesterday's archived snapshot
python pipeline/snapshot.py daily

# Build or rebuild a specific date
python pipeline/snapshot.py daily --date 2026-04-13
python pipeline/snapshot.py daily --date 2026-04-12 --force

# Rehearsal daily snapshots from the 2026-04-13 stand-in baseline.
# Stop rehearsal before the 2026-04-20 official genesis day.
python pipeline/snapshot.py daily --date 2026-04-14
python pipeline/snapshot.py backfill --start 2026-04-15 --end 2026-04-19

# Validation experiment: replay bounded gp_history from Jan 1 to now
# from an empty state, then compare the replayed state to current gp.
# This validates the bounded API shape; it is not a full historical bootstrap.
python pipeline/snapshot.py replay --start 2026-01-01

# Verify a stored snapshot
python pipeline/snapshot.py verify --date 2026-04-12

# Validate every archived snapshot, manifest, ledger entry, delta, and audit
python pipeline/snapshot.py validate
```

Useful operational knobs:

```bash
# Defaults: range size 10000, max catalog id 339999, minimum objects 40000.
# For long replay/backfill runs, use a larger delay to stay well below API caps.
RSO_REQUEST_DELAY=12.5 python pipeline/snapshot.py replay --start 2026-01-01
```

### Replay Findings

The Jan 1-to-current validation run confirms the API strategy, but also sets an
important boundary on what historical backfill can prove.

- Bounded 24-hour `gp_history` windows worked without the out-of-bounds
  Space-Track error that unbounded `<cutoff` queries produced.
- The replay processed 8.35M history rows across 103 windows using 50k
  `NORAD_CAT_ID` chunks and a 15 second inter-request delay.
- Starting from an empty Jan 1 state reconstructed 31,412 objects. Current `gp`
  contained 67,052 objects at the comparison observation time, so 35,640 current
  objects had no post-Jan-1 history rows and cannot be recovered by a
  delta-only replay.
- Of the 31,412 shared objects, 31,310 byte-matched current `gp`; 102 differed.
  Those differences confirm that current `gp` is a useful audit observation but
  not a perfect deterministic reconstruction target.

Operational conclusion: use `genesis` once to create the first agreed full
catalog from current `gp`, then use bounded `gp_history` deltas for every
subsequent daily consensus snapshot.

## Join the Operators

An operator is anyone running an independent copy of the archive pipeline. The
goal is simple: start from the same agreed baseline, run the same code, publish
the same daily hashes, and make drift visible.

The resilience comes from many independent operators, not from one blessed
server. If one GitHub account, one workflow, one maintainer, or one future
storage provider disappears, other operators still have the code, the data, the
ledger, and the hashes. Matching hashes across independent forks are the signal
that the public record is being witnessed, not merely hosted.

### 1. Create Your Space-Track Account

Create a free account at [Space-Track.org](https://www.space-track.org/auth/createAccount).
Use a real email address and read the Space-Track terms of use. The archive
uses only public GP/OMM data, but Space-Track rate limits still apply.

After the account is approved, keep the username and password ready. They will
be stored as GitHub Actions secrets, not committed to the repo.

### 2. Fork the Archive

Fork this repository into your own GitHub account or organization using the
GitHub **Fork** button. Your fork is your independent witness node.

The archive data is intentionally committed to Git during Phase 1. Do not
ignore `data/` or `ledger.json`; those files are the public record until
Arweave storage is added.

### 3. Enable GitHub Actions

Open your fork on GitHub, then go to:

```text
Settings -> Actions -> General
```

Enable Actions for the repository.

The producer workflows need permission to commit new archive files. Under
Workflow permissions, choose read/write access if GitHub lets you. If the option
is greyed out, your organization may be enforcing a stricter policy. The
workflows already declare:

```yaml
permissions:
  contents: write
```

If the daily workflow fails during `git push`, enable write permissions at the
organization level or use a fine-grained repository token with `Contents: Read
and write`.

### 4. Add Space-Track Secrets

Open:

```text
Settings -> Secrets and variables -> Actions -> Repository secrets
```

Add:

```text
SPACETRACK_USER = your Space-Track email
SPACETRACK_PASS = your Space-Track password
```

Do not put these values in `.env`, workflow YAML, commits, issues, or logs.

Optional command-line alternative: if you already use the GitHub CLI, these two
commands do the same secret setup as the web UI above. Skip this block if you
used the GitHub website.

```bash
gh secret set SPACETRACK_USER --repo YOUR_OWNER/RSO
gh secret set SPACETRACK_PASS --repo YOUR_OWNER/RSO
```

### 5. Know the Workflows

There are three workflows:

- **Validate RSO Archive** — read-only tests and archive validation. It needs no
  Space-Track credentials and runs on pushes, pull requests, and manual dispatch.
- **Daily RSO Snapshot** — scheduled producer workflow. It reads Space-Track,
  writes `data/`, and updates `ledger.json`.
- **Backfill RSO Archive** — manual producer workflow for bounded date ranges.

The validator is the first thing to trust. It proves the committed archive is
internally consistent without asking Space-Track for anything.

### 6. Run the Read-Only Validator

In GitHub:

```text
Actions -> Validate RSO Archive -> Run workflow
```

It should pass without secrets.

Optional local alternative: if you are comfortable using a terminal, these
commands do the same validation on your own machine. Skip this block if you are
only using GitHub Actions.

```bash
python3 -m unittest discover -s tests
python3 -m py_compile pipeline/snapshot.py
python3 pipeline/snapshot.py validate
```

The validator checks catalog hashes, canonical JSON, manifest fields, object
counts, ledger entries, rolling base hashes, delta counts, and audit artifact
consistency.

### 7. Run a Rehearsal Genesis

Before the official baseline day, test your producer setup with a rehearsal
baseline.

In GitHub:

```text
Actions -> Daily RSO Snapshot -> Run workflow
```

Use:

```text
date  = today's UTC date
mode  = genesis
force = true only if that date already exists in your fork
```

This captures current `gp`, writes a `genesis_from_gp` snapshot for that date,
updates `ledger.json`, and commits the result back to your fork.

Confirm the run output includes:

```text
Provenance: genesis_from_gp
Objects:    50,000+
SHA-256:    ...
DONE
```

Then confirm the follow-up **Validate RSO Archive** workflow is green.

### 8. Rehearse One Daily Roll-Forward

After a rehearsal genesis exists, run **Daily RSO Snapshot** manually for the
next date:

```text
date  = next UTC date after your rehearsal genesis
mode  = daily
force = false unless deliberately rebuilding that date
```

That run should create a `rolling_gp_history_delta` snapshot with:

```text
catalog.json.gz
manifest.json
delta.json
audit.json
visibility_state.json
```

The manifest should point back to the prior snapshot with
`base_snapshot_date` and `base_snapshot_sha256`. The validator checks that link.

### 9. Let the Schedule Run

The daily producer is scheduled for:

```text
12:15am Pacific
07:15 UTC while Pacific daylight time is in effect
```

GitHub schedules are not exact to the second and may start a few minutes late.
That is fine. The canonical data cutoff remains midnight UTC.

The official archive baseline date is `2026-04-20`. On that date, the workflow
automatically runs:

```bash
python pipeline/snapshot.py genesis --date 2026-04-20
```

On other dates, it runs:

```bash
python pipeline/snapshot.py daily --date YYYY-MM-DD
```

### 10. Check Your Daily Result

Each successful producer run should commit archive changes back to your fork.
Check:

```text
Actions -> Daily RSO Snapshot -> latest run -> success
Actions -> Validate RSO Archive -> latest run -> success
```

For a daily snapshot, inspect the new manifest:

```text
data/YYYY/MM/DD/manifest.json
```

The important fields are:

```text
date
sha256
object_count
provenance
base_snapshot_date
base_snapshot_sha256
delta_window_start_utc
delta_window_end_utc
```

For the audit, inspect:

```text
data/YYYY/MM/DD/audit.json
```

The audit is not part of the consensus hash path. It is a time-sampled current
`gp` observation that makes missing or reappearing objects visible.

### 11. Compare With Other Operators

Decentralization only helps if operators compare results. The expected healthy
state is boring: independent forks publish the same hash for the same date.

For the same date and same baseline lineage, operators should compare:

```text
ledger.json date
ledger.json sha256
manifest.json sha256
manifest.json object_count
```

Matching hashes mean the canonical catalogs match byte-for-byte after
decompression. If hashes diverge, compare `manifest.json` first, then
`delta.json`, then the workflow logs. Most disagreements should reduce to a
different baseline, a rebuild with `--force`, or a Space-Track response
difference inside the bounded `gp_history` window.

### 12. Be Gentle With the API

The workflows set conservative delays between Space-Track requests:

```text
Daily:   RSO_REQUEST_DELAY=5
Backfill: RSO_REQUEST_DELAY=12.5
```

Avoid repeated forced rebuilds. Do not run large backfills from multiple forks
at the same time unless there is a clear reason. The archive should be boring,
repeatable, and polite.

## Data Structure

```
data/
├── 2026/
│   ├── 01/
│   │   ├── 01/
│   │   │   ├── catalog.json.gz    # Compressed GP catalog snapshot
│   │   │   ├── manifest.json      # Hash, object count, metadata
│   │   │   ├── delta.json         # Bounded gp_history changes applied
│   │   │   ├── audit.json         # Time-sampled current-gp visibility audit
│   │   │   └── visibility_state.json
│   │   ├── 02/
│   │   │   ├── catalog.json.gz
│   │   │   └── manifest.json
│   │   └── ...
│   └── ...
└── ledger.json                     # Running hash ledger (all dates)
```

### Manifest Example

```json
{
  "date": "2026-04-12",
  "cutoff_utc": "2026-04-12T00:00:00Z",
  "state_as_of_utc": "2026-04-12T00:00:00Z",
  "sha256": "a1b2c3d4e5f6...",
  "object_count": 50847,
  "raw_bytes": 48293847,
  "compressed_bytes": 8234561,
  "provenance": "rolling_gp_history_delta",
  "format": "OMM/JSON",
  "source": "space-track.org",
  "pipeline_version": "0.3.0",
  "query_strategy": "prior_snapshot_plus_bounded_gp_history_delta",
  "base_snapshot_date": "2026-04-11",
  "base_snapshot_sha256": "9f8e7d6c5b4a...",
  "delta_window_start_utc": "2026-04-11T00:00:00Z",
  "delta_window_end_utc": "2026-04-12T00:00:00Z",
  "api_query_base": "https://www.space-track.org/basicspacedata/query",
  "api_query_paths": [
    "/class/gp_history/CREATION_DATE/2026-04-11T00:00:00--2026-04-12T00:00:00/orderby/NORAD_CAT_ID%20asc,CREATION_DATE%20desc/format/json"
  ],
  "archived_at": "2026-04-12T07:15:12.456789+00:00"
}
```

## Verification

Anyone can verify a snapshot independently:

1. Download `catalog.json.gz` for a given date
2. Decompress it
3. Compute SHA-256 of the raw bytes
4. Compare against `manifest.json` or `ledger.json`

```bash
# Quick verify
python pipeline/snapshot.py verify --date 2026-04-12
```

```bash
# Manual verify
gunzip -k data/2026/04/12/catalog.json.gz
sha256sum data/2026/04/12/catalog.json
```

## Tests

The project intentionally has no external dependencies.

```bash
python -m unittest discover -s tests
```

## Roadmap

- [x] Daily rolling snapshot pipeline
- [x] Backfill from an existing prior-day base snapshot
- [x] Local hash verification
- [x] Refactor daily snapshots to midnight UTC rolling `gp_history` deltas
- [x] Add current `gp` visibility audit and missing/reappeared state
- [x] Analyze Jan 1-to-current replay results against current `gp`
- [ ] Arweave permanent upload (via Irys)
- [ ] Ethereum contract for hash attestation
- [ ] Daily diff computation (objects added/updated/carried-forward)
- [ ] TDH-weighted community confirmations
- [ ] Dynamic NFT artwork (6529 The Memes)
- [ ] Weekly Merkle root on-chain checkpoints
- [ ] Orbital Witness template for additional datasets
- [ ] NEO witness archive

## Prior Art & Acknowledgments

- **MITRE BESTA** (Dailey, Reed, Bryson, 2019–2020) — Established the conceptual case that blockchain and space situational awareness belong together.
- **Dr. T.S. Kelso / CelesTrak** — Decades of making GP data accessible when nobody else would.
- **Jonathan McDowell / GCAT** — Proving one person's determination can preserve the space record. "My audience is the historian 1,000 years from now."
- **18th Space Defense Squadron / Space-Track** — Making the data public in the first place.
- **6529 / The Memes / Open Metaverse** — Building the community infrastructure (TDH, delegation, the open metaverse thesis) that makes this approach possible.

## License

CC0 1.0 Universal

---

*The community is the infrastructure. The art is the dashboard. The meme is the message.*
