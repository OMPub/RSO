# RSO Operator Guide

This guide is for someone who wants to operate an independent Orbital Witness
node. It is written as a detailed, beginner-friendly resource for people who
want the full path explained clearly, and questions or requests for
clarification are welcome.

## Why do this?

The public space object catalog is important, but fragile:

- One source publishes it: Space-Track (relies on government funding and staff).
- One person mirrors it publicly at CelesTrak.
- Without multiple independent archives, later edits or removals are hard to prove.

Operating a witness node means you keep your own copy of the code, run the same
daily snapshot logic, and publish the same hash chain from your own GitHub
fork. If many operators get the same result independently, the archive is being
witnessed, not merely hosted.

## What you need

Minimum:

- A GitHub account
- A free Space-Track account
- Permission to enable GitHub Actions on your fork

Helpful but optional:

- Git installed on your laptop
- Python 3.10+ if you want to validate or inspect locally

Important distinction:

- A **fork** is your copy of this repo on GitHub.
- A **clone** is a copy of your fork on your laptop.

You can operate with GitHub Actions only. A local clone is still useful because
it lets you inspect files, run validation, and understand what the workflows
actually wrote.

The intended setup path is deliberately short: fork the repo, enable Actions,
add the two Space-Track secrets, and let the scheduled workflow run. The latest
full catalog state needed for the first daily roll-forward is already committed
in the repo.

## What success looks like

A healthy operator run produces four visible things:

1. A green workflow run in the **Actions** tab
2. A new daily metadata folder under `data/YYYY/MM/DD/`
3. An updated `ledger.json`
4. A release asset named `rso-archive-YYYY-MM-DD.tar.gz`

For a normal daily snapshot, the committed day folder should contain:

- `manifest.json`
- `delta.json`
- `audit.json`
- `visibility_state.json`

The latest two archived days also keep `catalog.json.gz` in Git. That small
rolling cache is what makes a fresh fork self-starting: the workflow can read
the prior full catalog directly from the fork before it has published any of
its own release bundles.

## First-time path

### 1. Fork the repository

On GitHub, open this repo and press **Fork**.

That creates your operator copy at:

```text
https://github.com/YOUR_USERNAME/RSO
```

### 2. Clone your fork

Optional for GitHub-only operation, but recommended:

```bash
git clone git@github.com:YOUR_USERNAME/RSO.git
cd RSO
```

If you use HTTPS instead of SSH:

```bash
git clone https://github.com/YOUR_USERNAME/RSO.git
cd RSO
```

### 3. Enable GitHub Actions

In your fork:

```text
Settings -> Actions -> General
```

Enable Actions.

Then check workflow write access:

```text
Settings -> Actions -> General -> Workflow permissions
```

Choose read/write access if GitHub offers it. The daily workflow needs to commit
archive metadata back into your fork.

### 4. Add your Space-Track credentials

In your fork:

```text
Settings -> Secrets and variables -> Actions -> Repository secrets
```

Create:

```text
SPACETRACK_USER
SPACETRACK_PASS
```

These are the only required secrets for the current GitHub-release operator
path.

### 5. Run the validator first

Before pulling live data, prove your fork can run the read-only checks. The
validator also confirms that the latest retained `catalog.json.gz` files are
present and match their manifests.

On GitHub:

```text
Actions -> Validate RSO Archive -> Run workflow
```

Expected result: green.

### 6. Understand the official genesis

The official chain already starts at `2026-04-20`. New operators normally do
not create a fresh genesis document. They validate the existing lineage and
then continue it.

If you want to inspect the first document in the chain, look at:

```text
data/2026/04/20/manifest.json
```

That document is the agreed `genesis_from_gp` baseline for the live archive.

### 7. Run the next daily roll-forward

After Actions are enabled, the scheduled workflow should run automatically each
day. If your fork is behind, it starts at the next missing date, backfills
through yesterday, and then runs the current daily snapshot. You can also start
the first run manually instead of waiting for the next 00:15 UTC schedule.

On GitHub:

```text
Actions -> Daily RSO Snapshot -> Run workflow
```

Use:

```text
mode = auto
date = blank, unless you deliberately want one specific date
force = false unless deliberately rebuilding an existing date
```

Expected result:

- workflow succeeds
- a new `data/YYYY/MM/DD/manifest.json` appears for the next day
- `ledger.json` updates
- `catalog.json.gz` remains committed for the two newest archived days
- a matching release asset appears

That proves your fork can:

- read the prior snapshot
- apply a bounded `gp_history` delta
- write the new manifest/audit files
- publish the release bundle

### 8. Compare with another operator

For the same date, compare:

- `ledger.json` hash
- `manifest.json` hash
- `object_count`

Matching hashes across forks are the real success condition.

## Where to look when you are lost

- `README.md`: full technical walkthrough and command reference
- `GLOSSARY.md`: orbital-data terms and field definitions
- `data/YYYY/MM/DD/manifest.json`: the daily hash and provenance summary
- `ledger.json`: rolling public hash chain
- `Releases`: where the full daily bundle is published
- `reports/rehearsal/`: pre-baseline practice data, separate from the official lineage

## The two outputs to remember

Every successful producer run writes to two places:

- Git metadata: `data/` and `ledger.json`
- Release bundle: `rso-archive-YYYY-MM-DD.tar.gz`

Git also keeps a rolling cache of the newest two `catalog.json.gz` files. Older
full catalogs are pruned from Git after their deterministic release bundles are
built. That split keeps the repo small while making new forks able to continue
the chain without manual bootstrapping.

The daily hash comes from the canonical snapshot bytes, not from the release
URL or storage location. Different operators can publish the same bytes in
different places and still agree on the same daily hash.

## If something fails

Most first-run failures are one of these:

- Actions not enabled
- Workflow permissions still read-only
- `SPACETRACK_USER` or `SPACETRACK_PASS` missing
- A date already exists and needs `force = true`

If the daily workflow can read Space-Track but fails on `git push`, check
workflow write permissions first.
