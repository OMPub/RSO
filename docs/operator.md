# RSO Operator and Sweeper Model

An RSO operator runs an archive node. The node downloads public Space-Track
data, produces the daily archive artifact, and optionally signs a DocChain
attestation with a disposable no-funds EOA.

A sweeper is different. It is a funded courier that gathers public signed
attestations from known operators and submits eligible ones to the DocChain
contract.

## Operator Role

An operator node should be boring:

- run the daily snapshot workflow
- publish the release bundle
- optionally publish a signed DocChain artifact
- keep the disposable signing key unfunded

The operator signing key is not a personal wallet. It should hold no ETH, NFTs,
or tokens. If it leaks, there are no funds to steal. The damage is reputational:
an attacker could sign bad claims until the operator rotates the key and backing
relationship.

## Card-Backed Operator Support

Operators do not need to connect their disposable key to a 6529 identity in RSO
V1. Card holders back nodes, not signing addresses. That lets operators rotate
disposable signing keys without asking backers to move, and lets card holders
switch support as node performance changes.

Recommended V1 shape:

```text
card holder -> backs node
```

The daily signed attestation sets:

```text
attester    disposable EOA
onBehalfOf  normally zero for RSO V1
```

After the daily 6529 TDH calculation, RSO computes a backing snapshot:

```text
date -> node id -> card-specific TDH backing
```

The sweeper uses that snapshot before spending treasury gas. The indexer uses
the same snapshot when reporting weighted agreement groups. For GitHub-hosted
nodes, the node id is `github:owner/repo`.

## Sweeper Role

The sweeper:

- reads a known operator registry or discovers candidate repositories from the
  upstream GitHub fork graph
- reads the daily operator-backing snapshot
- extracts each signed artifact's attester and ranks only backed nodes by
  card-specific TDH backing
- fetches signed artifacts from operator `node` branches
- validates signatures by simulating `attestDoc`
- validates the signed archive bundle fingerprint and the catalog fingerprint
- submits valid claims for selected backed operators with a funded sweeper
  wallet

The sweeper does not decide truth. If it refuses to submit a valid signature,
the public signature can still be submitted by anyone else.

Fork discovery is not a trust signal. It only tells the sweeper where to look.
Sponsorship still requires a signed artifact from a node present in the daily
backing snapshot.

## Treasury Role

The NFT treasury funds shared infrastructure:

- Arweave publishing
- sweeper gas
- public indexes and monitoring

The treasury should not be the only attester. Truth comes from reproducible
daily artifacts plus independent operator signatures. The treasury only pays to
publish eligible signatures.

## Failure Modes

If an operator key leaks:

- remove or pause that operator in the sweeper registry
- ask backers to move support to the new disposable EOA
- publish the key rotation in operator docs

If the sweeper fails:

- signed artifacts remain public
- another sweeper can submit them
- anyone can submit a valid signature manually
- the next scheduled sweep can retry missed signed artifacts that remain
  eligible and discoverable

The sweeper publishes public per-date reports under:

```text
reports/sweeper/YYYY-MM-DD.json
```

Node repos include a default **Check RSO Sweeper Report** workflow. It reads the
public report for its own `nodeId` and opens, updates, or closes one local issue
when the report shows a condition that may need operator attention. Operators do
not need to monitor Actions artifacts manually.

If a signed attestation lists multiple publication locations, the sweeper checks
every listed location before submitting it onchain. Temporary fetch failures are
retried immediately, then deferred to a later sweep. Locators with too many
locations are rejected to keep one operator from consuming unbounded treasury
resources.

If operators disagree:

- the contract records all valid claims
- the indexer groups matching and conflicting fingerprints
- the UI reports support behind each group

Disagreement is not hidden. It is the evidence the archive is meant to surface.
