# Deep-history rebuild — verification record

Full 1957→2025 rebuild run **2026-06-24** on the Mac Studio (echo-base, M3 Ultra,
512 GB, Python 3.9.6), engine `pipeline/build_history.py`, schema
`rso-core-omm-v1` (11-field pure-orbit core). Artifacts on the Studio at
`/Users/Shared/Backups/rso/rebuild/out/`.

## Rebuild summary
| metric | value |
|---|---|
| elsets processed | 232,331,940 |
| elsets skipped (logged) | 48,614 (0.021%) |
| daily catalogs | 24,462 |
| genesis | 1959-01-11 |
| final day | 2025-12-31 |
| final catalog size | 65,329 objects |
| wall time | 964 s (~16 min, 28 workers) |
| genesis hash | `b1a567a2…8090ca` |
| final hash | `4bf23033…c75f904e` |

## Checks

**1. Continuity — PASS.** 1959-01-11 → 2025-12-31 is *exactly* 24,462 calendar
days (66y + leap days + Jan-Dec 2025); manifest has 24,462 rows, zero duplicate
days, zero gaps. Every UTC day present exactly once by construction (day-sweep).

**2. Skip categorization — PASS (no hidden data loss).** All 48,614 skips are a
single class — `malformed assumed-exponent field` (47,833 + 778 + 2) plus 1
`non-numeric decimal field`. These are the non-standard **overflowed drag-term
encoding** on rapidly-decaying (near-reentry) objects, which the §4 decoder
**fails closed** on (never guesses). No systematic category of valid data is
silently dropped. Carry-forward covers the affected objects (they keep their last
valid elset). Every skipped line is preserved in `out/skipped_elsets.tsv`,
recoverable later if the overflow format is deciphered.

**3. Growth curve — PASS (matches known catalog history).**
1965: 887 · 1975: 6,701 · 1985: 14,049 · 1995: 21,548 · 2005: 26,362 ·
2015: 37,888 · 2024: 56,940 · 2025-12-31: 65,329. Monotonic, with the visible
Starlink-era acceleration 2015→2024.

**4. Independent recomputation — cross-validates the sort+sweep path.**
`pipeline/verify_days.py` recomputes selected days' hashes by a *different*
aggregation (per-object "latest EPOCH ≤ D" reduction — no external sort, no
day-sweep), sharing only the unit-tested §4 canonicalizer. Local self-test vs the
1959–65 slice manifest: 1959-12-31, 1962-06-15, 1965-12-31 all MATCH (hash +
count). Full-manifest run across 1959→2025 (incl. both endpoints + leap days
2000-02-29 / 2024-02-29): **ALL 11 MATCH** — hash + object count identical on
every sampled day. Endpoints anchored (1959-12-31 = genesis era; 2025-12-31 =
`4bf23033…` = recorded final hash).

**5. Parser provenance.** The §4 canonicalizer is independently validated by 60
unit tests + the §6 reference vectors (ISS TLE⇔OMM, Sputnik, 1959 Vanguard
legacy export) + prior numeric spot-checks vs the live Space-Track API.

## Result
**VERIFIED.** The 24,462-day deep-history rebuild is structurally complete
(perfect day continuity), has no hidden data loss (all skips one known
fail-closed class, fully logged), tracks the real catalog growth curve, and
every sampled day across the full 1959→2025 timeline reproduces bit-for-bit via
an independent aggregation. The manifest (`out/daily_manifest.txt`) is the
authoritative per-day `contentHash` + `recordCount` set, ready for the §6 Merkle
month-roots + on-chain attestation.

Open follow-ons (not blockers): McDowell 1957-58 genesis graft (extends genesis
from 1959-01-11 back to 1957-10-04); optional tolerant decoder to recover the
~48k overflowed-drag-term skips; Merkle/blockHash linkage + mainnet attestation
(needs chain context + go-ahead).
