"""Stdlib RSO sweeper for treasury-submitted signed DocChain attestations."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import ipaddress
import io
import json
import os
import re
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indexer.rso_profile import RSO_DOC_CHAIN_ID, describe_publication_uri, doc_ref_to_date  # noqa: E402
from vendor.docchain.attestation import (  # noqa: E402
    attest_doc_calldata,
    cast_wallet_args_from_env,
    cast_path,
    normalize_address,
    normalize_bytes32,
    normalize_hex_bytes,
    subprocess_error_detail,
)
from vendor.docchain.indexer import EthereumRpc, RpcError  # noqa: E402


ARWEAVE_GATEWAY = "https://arweave.net"
DEFAULT_SPONSORSHIP_LIMIT = 5


class SweeperError(ValueError):
    """Validation error for a signed attestation or sweeper configuration."""


@dataclass(frozen=True)
class SweeperConfig:
    rpc_url: str
    docchain_address: str
    private_key_env: str = "RSO_SWEEPER_PRIVATE_KEY"
    cast: str | None = None
    dry_run: bool = False
    require_uri: bool = True
    max_bundle_bytes: int = 100 * 1024 * 1024
    max_catalog_bytes: int = 200 * 1024 * 1024
    max_json_bytes: int = 2 * 1024 * 1024
    max_publication_locations: int = 4
    fetch_retries: int = 2
    fetch_retry_delay: float = 5.0
    request_timeout: float = 30.0
    confirmations: int = 1


def config_from_env() -> SweeperConfig:
    rpc_url = os.environ.get("RSO_SWEEPER_RPC_URL") or os.environ.get("DOCCHAIN_RPC_URL") or os.environ.get("RPC_URL")
    docchain_address = os.environ.get("RSO_DOCCHAIN_ADDRESS") or os.environ.get("DOCCHAIN_ADDRESS")
    if not rpc_url:
        raise SweeperError("set RSO_SWEEPER_RPC_URL, DOCCHAIN_RPC_URL, or RPC_URL")
    if not docchain_address:
        raise SweeperError("set RSO_DOCCHAIN_ADDRESS or DOCCHAIN_ADDRESS")
    return SweeperConfig(
        rpc_url=rpc_url,
        docchain_address=normalize_address(docchain_address),
        private_key_env=os.environ.get("RSO_SWEEPER_PRIVATE_KEY_ENV", "RSO_SWEEPER_PRIVATE_KEY"),
        cast=os.environ.get("CAST"),
        dry_run=env_bool("RSO_SWEEPER_DRY_RUN", False),
        require_uri=not env_bool("RSO_ALLOW_HASH_ONLY_ATTESTATIONS", False),
        max_bundle_bytes=int(os.environ.get("RSO_SWEEPER_MAX_BUNDLE_BYTES", str(100 * 1024 * 1024))),
        max_catalog_bytes=int(os.environ.get("RSO_SWEEPER_MAX_CATALOG_BYTES", str(200 * 1024 * 1024))),
        max_json_bytes=int(os.environ.get("RSO_SWEEPER_MAX_JSON_BYTES", str(2 * 1024 * 1024))),
        max_publication_locations=int(os.environ.get("RSO_SWEEPER_MAX_PUBLICATION_LOCATIONS", "4")),
        fetch_retries=int(os.environ.get("RSO_SWEEPER_FETCH_RETRIES", "2")),
        fetch_retry_delay=float(os.environ.get("RSO_SWEEPER_FETCH_RETRY_DELAY", "5")),
        request_timeout=float(os.environ.get("RSO_SWEEPER_REQUEST_TIMEOUT", "30")),
        confirmations=int(os.environ.get("RSO_SWEEPER_CONFIRMATIONS", "1")),
    )


def load_operator_registry_source(source: str, timeout: float) -> list[dict[str, object]]:
    if source.startswith("github-forks:"):
        return github_fork_operator_registry(
            source.removeprefix("github-forks:"),
            timeout=timeout,
        )
    return load_operator_registry(Path(source))


def load_operator_registry(path: Path) -> list[dict[str, object]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SweeperError("operator registry must be a JSON object")
    if raw.get("schema") != "rso-sweeper-operators-v1":
        raise SweeperError("unsupported operator registry schema")
    operators = raw.get("operators")
    if not isinstance(operators, list):
        raise SweeperError("operator registry requires an operators array")
    return [operator for operator in operators if isinstance(operator, dict) and operator.get("enabled", True)]


def github_fork_operator_registry(repo: str, *, timeout: float) -> list[dict[str, object]]:
    """Discover candidate operator repositories from the public GitHub fork graph."""
    root_repo = normalize_github_repo(repo)
    max_forks = int(os.environ.get("RSO_SWEEPER_MAX_FORKS", "100"))
    if max_forks < 0:
        raise SweeperError("RSO_SWEEPER_MAX_FORKS must not be negative")
    operators: list[dict[str, object]] = [
        {
            "name": root_repo,
            "nodeId": github_node_id(root_repo),
            "repository": root_repo,
            "branch": "node",
            "source": "github-root",
        }
    ]
    seen = {root_repo.lower()}
    page = 1
    while len(operators) - 1 < max_forks:
        per_page = min(100, max_forks - (len(operators) - 1))
        if per_page <= 0:
            break
        url = (
            f"https://api.github.com/repos/{root_repo}/forks"
            f"?per_page={per_page}&page={page}&sort=newest"
        )
        forks = fetch_github_json_array(url, timeout)
        if not forks:
            break
        for fork in forks:
            if not isinstance(fork, Mapping):
                continue
            full_name = fork.get("full_name")
            if not isinstance(full_name, str):
                continue
            full_name = normalize_github_repo(full_name)
            if full_name.lower() in seen:
                continue
            seen.add(full_name.lower())
            operators.append(
                {
                    "name": full_name,
                    "nodeId": github_node_id(full_name),
                    "repository": full_name,
                    "branch": "node",
                    "source": "github-fork",
                }
            )
        if len(forks) < per_page:
            break
        page += 1
    return operators


def normalize_github_repo(repo: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise SweeperError("GitHub repository must be OWNER/REPO")
    return repo


def github_node_id(repo: str) -> str:
    return "github:" + normalize_github_repo(repo).lower()


def operator_node_id(operator: Mapping[str, object]) -> str:
    raw_node_id = operator.get("nodeId")
    if isinstance(raw_node_id, str) and raw_node_id:
        return normalize_node_id(raw_node_id)
    repository = operator.get("repository")
    if isinstance(repository, str) and repository:
        return github_node_id(repository)
    raise SweeperError("operator requires nodeId or repository")


def normalize_node_id(value: str) -> str:
    text = value.strip().lower()
    if not text:
        raise SweeperError("nodeId must not be empty")
    if text.startswith("github:"):
        return github_node_id(text.removeprefix("github:"))
    if "/" in text and ":" not in text:
        return github_node_id(text)
    if text.startswith("domain:"):
        host = text.removeprefix("domain:")
        if "/" in host or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", host):
            raise SweeperError("domain nodeId must be domain:hostname")
        return "domain:" + host
    raise SweeperError("nodeId must use a supported scheme such as github:OWNER/REPO")


def fetch_github_json_array(url: str, timeout: float) -> list[object]:
    headers = {"user-agent": "rso-sweeper/1", "accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["authorization"] = "Bearer " + token
    body = fetch_url_bytes_with_redirects(
        url,
        timeout=timeout,
        max_bytes=int(os.environ.get("RSO_SWEEPER_MAX_JSON_BYTES", str(2 * 1024 * 1024))),
        headers=headers,
        label="GitHub fork graph",
        allow_authorized_redirects=False,
    )
    raw = json.loads(body.decode("utf-8"))
    if not isinstance(raw, list):
        raise SweeperError("GitHub fork graph response must be a JSON array")
    return raw


def load_backing_snapshot(location: str, snapshot_date: str, timeout: float) -> dict[str, dict[str, object]]:
    raw = fetch_json_location(backing_snapshot_location(location, snapshot_date), timeout)
    return normalize_backing_snapshot(raw, expected_date=snapshot_date)


def backing_snapshot_location(location: str, snapshot_date: str) -> str:
    if "{date}" in location:
        return location.format(date=snapshot_date)
    parsed = urllib.parse.urlparse(location)
    if parsed.scheme == "":
        path = Path(location)
        if path.is_dir():
            return str(path / f"{snapshot_date}.json")
    return location


def fetch_json_location(location: str, timeout: float) -> dict[str, object]:
    parsed = urllib.parse.urlparse(location)
    if parsed.scheme in ("https", "ar"):
        return fetch_json_url(location if parsed.scheme == "https" else publication_url(location), timeout)
    raw = json.loads(Path(location).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SweeperError("backing snapshot must be a JSON object")
    return raw


def normalize_backing_snapshot(
    raw: Mapping[str, object],
    *,
    expected_date: str | None = None,
) -> dict[str, dict[str, object]]:
    schema = raw.get("schema")
    if schema not in (None, "rso-operator-backing-snapshot-v1", "rso-operator-backing-v1"):
        raise SweeperError("unsupported backing snapshot schema")
    snapshot_date = str(raw.get("date", ""))
    if expected_date is not None and snapshot_date and snapshot_date != expected_date:
        raise SweeperError("backing snapshot date does not match swept date")
    operators = raw.get("operators")
    if operators is None and schema == "rso-operator-backing-v1":
        operators = raw.get("identities")
    if operators is None and schema is None:
        operators = raw
    normalized: dict[str, dict[str, object]] = {}
    if isinstance(operators, Mapping):
        for node_id, record in operators.items():
            normalized_node_id = normalize_node_id(str(node_id))
            normalized[normalized_node_id] = normalize_backing_record(
                node_id=normalized_node_id,
                record=record,
                snapshot_date=snapshot_date,
            )
    elif isinstance(operators, list):
        for record in operators:
            if not isinstance(record, Mapping):
                raise SweeperError("operator backing records must be JSON objects")
            node_id = record.get("nodeId") or record.get("node") or record.get("operator") or record.get("repository")
            if not isinstance(node_id, str):
                raise SweeperError("operator backing record requires nodeId")
            normalized_node_id = normalize_node_id(node_id)
            normalized[normalized_node_id] = normalize_backing_record(
                node_id=normalized_node_id,
                record=record,
                snapshot_date=snapshot_date,
            )
    else:
        raise SweeperError("backing snapshot requires operators")
    return normalized


def normalize_backing_record(
    *,
    node_id: str,
    record: object,
    snapshot_date: str = "",
) -> dict[str, object]:
    if isinstance(record, Mapping):
        card_specific_tdh = int(record.get("cardSpecificTdh", 0))
        backer_count = int(record.get("backerCount", 0))
        rank_raw = record.get("rank")
    else:
        card_specific_tdh = int(record)
        backer_count = 0
        rank_raw = None
    if card_specific_tdh < 0:
        raise SweeperError("cardSpecificTdh must not be negative")
    if backer_count < 0:
        raise SweeperError("backerCount must not be negative")
    rank = int(rank_raw) if rank_raw is not None else 0
    if rank < 0:
        raise SweeperError("rank must not be negative")
    return {
        "nodeId": normalize_node_id(node_id),
        "cardSpecificTdh": card_specific_tdh,
        "backerCount": backer_count,
        "rank": rank,
        "snapshotDate": snapshot_date,
    }


def eligible_operators(
    operators: list[dict[str, object]],
    backing: Mapping[str, dict[str, object]],
    *,
    limit: int,
    min_card_specific_tdh: int,
) -> list[dict[str, object]]:
    if limit < 0:
        raise SweeperError("sponsorship limit must not be negative")
    if min_card_specific_tdh < 0:
        raise SweeperError("minimum card-specific TDH must not be negative")
    eligible = []
    for operator in operators:
        node_id = operator_node_id(operator)
        backing_record = backing.get(node_id)
        if not backing_record:
            continue
        if int(backing_record["cardSpecificTdh"]) < min_card_specific_tdh:
            continue
        enriched = dict(operator)
        enriched["nodeId"] = node_id
        enriched["_backing"] = dict(backing_record)
        eligible.append(enriched)
    eligible.sort(key=operator_sponsorship_sort_key)
    return eligible if limit == 0 else eligible[:limit]


def operator_sponsorship_sort_key(operator: Mapping[str, object]) -> tuple[int, int, str]:
    backing = operator.get("_backing")
    if not isinstance(backing, Mapping):
        raise SweeperError("eligible operator is missing backing")
    rank = int(backing.get("rank", 0))
    rank_sort = rank if rank > 0 else 1_000_000_000
    return (-int(backing.get("cardSpecificTdh", 0)), rank_sort, str(operator.get("name", operator.get("attester", ""))))


def signed_attestation_url(operator: Mapping[str, object], snapshot_date: str) -> str:
    template = operator.get("signedAttestationUrlTemplate")
    if isinstance(template, str) and template:
        return template.format(date=snapshot_date)
    repository = str(operator.get("repository", ""))
    branch = str(operator.get("branch", "node"))
    if not repository or "/" not in repository:
        raise SweeperError("operator requires repository or signedAttestationUrlTemplate")
    return (
        "https://raw.githubusercontent.com/"
        + repository.strip("/")
        + "/"
        + urllib.parse.quote(branch, safe="")
        + "/data/attestations/signed/"
        + urllib.parse.quote(snapshot_date, safe="")
        + ".json"
    )


def operator_label(operator: Mapping[str, object]) -> str:
    return str(operator.get("name") or operator.get("repository") or operator.get("attester") or "operator")


def candidate_operator_payloads(
    operators: list[dict[str, object]],
    backing: Mapping[str, dict[str, object]],
    *,
    snapshot_date: str,
    config: SweeperConfig,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    candidates = []
    records = []
    for operator in operators:
        label = operator_label(operator)
        node_id = operator_node_id(operator)
        backing_record = backing.get(node_id)
        if not backing_record:
            records.append({"operator": label, "nodeId": node_id, "status": "not_backed"})
            continue
        expected_attester = operator.get("attester")
        url = signed_attestation_url(operator, snapshot_date)
        try:
            payload = fetch_json_url(
                url,
                config.request_timeout,
                retries=config.fetch_retries,
                retry_delay=config.fetch_retry_delay,
                max_bytes=config.max_json_bytes,
            )
            attester = attester_from_signed_payload(payload)
            if isinstance(expected_attester, str) and normalize_address(expected_attester) != attester:
                raise SweeperError("signed artifact attester does not match operator registry")
            enriched = dict(operator)
            enriched["nodeId"] = node_id
            enriched["attester"] = attester
            enriched["_backing"] = dict(backing_record)
            enriched["_payload"] = payload
            enriched["_url"] = url
            candidates.append(enriched)
            records.append({"operator": label, "nodeId": node_id, "status": "candidate", "attester": attester})
        except urllib.error.HTTPError as exc:
            status = "missing" if exc.code == 404 else "deferred"
            records.append({"operator": label, "nodeId": node_id, "status": status, "error": f"HTTP {exc.code}", "url": url})
        except (OSError, SweeperError, ValueError) as exc:
            records.append({"operator": label, "nodeId": node_id, "status": "deferred", "error": str(exc), "url": url})
    candidates.sort(key=operator_sponsorship_sort_key)
    return candidates, records


def attester_from_signed_payload(payload: Mapping[str, object]) -> str:
    signed = extract_signed(payload)
    prepared = signed["prepared"]
    if not isinstance(prepared, Mapping):
        raise SweeperError("signed.prepared must be an object")
    attestation = prepared["attestation"]
    if not isinstance(attestation, Mapping):
        raise SweeperError("prepared.attestation must be an object")
    return normalize_address(attestation["attester"])


def fetch_json_url(
    url: str,
    timeout: float,
    *,
    retries: int = 2,
    retry_delay: float = 5.0,
    max_bytes: int = 2 * 1024 * 1024,
) -> dict[str, object]:
    raw = None
    for attempt in range(retries + 1):
        try:
            body = fetch_url_bytes_with_redirects(
                url,
                timeout=timeout,
                max_bytes=max_bytes,
                headers={"user-agent": "rso-sweeper/1"},
                label="signed attestation",
                allow_authorized_redirects=False,
            )
            raw = json.loads(body.decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if not http_error_retryable(exc) or attempt == retries:
                raise
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt == retries:
                raise SweeperError(f"fetch did not settle for {url}: {exc}") from exc
        time.sleep(retry_delay)
    if raw is None:
        raise SweeperError(f"fetch did not return JSON for {url}")
    if not isinstance(raw, dict):
        raise SweeperError("fetched signed attestation must be a JSON object")
    return raw


def extract_signed(payload: Mapping[str, object]) -> Mapping[str, object]:
    if payload.get("schema") == "doc-chain-signed-attestation-v1":
        return payload
    if payload.get("schema") == "rso-signed-attestation-v1":
        signed = payload.get("signed")
        if isinstance(signed, Mapping):
            return signed
    raise SweeperError("request must contain a signed DocChain attestation")


def validate_prepared_context(prepared: Mapping[str, object], config: SweeperConfig) -> None:
    if prepared.get("schema") != "doc-chain-prepared-attestation-v1":
        raise SweeperError("unsupported prepared attestation schema")
    if normalize_address(prepared.get("contractAddress")) != config.docchain_address:
        raise SweeperError("prepared contractAddress does not match sweeper contract")
    attestation = prepared["attestation"]
    if not isinstance(attestation, Mapping):
        raise SweeperError("prepared.attestation must be an object")
    doc_block = attestation["docBlock"]
    if not isinstance(doc_block, Mapping):
        raise SweeperError("docBlock must be an object")
    if normalize_bytes32(doc_block["docChainId"]) != RSO_DOC_CHAIN_ID:
        raise SweeperError("attestation is not for the RSO DocChain profile")
    doc_ref_to_date(str(doc_block["docRef"]))
    if int(attestation["deadline"]) < int(time.time()):
        raise SweeperError("attestation deadline has expired")


def validate_operator(operator: Mapping[str, object], attestation: Mapping[str, object]) -> str:
    attester = normalize_address(attestation["attester"])
    expected_attester = operator.get("attester")
    if expected_attester and normalize_address(expected_attester) != attester:
        raise SweeperError("signed attestation does not match registered operator attester")
    return attester


def sponsorship_record(operator: Mapping[str, object], attester: str) -> dict[str, object]:
    backing = operator.get("_backing")
    if not isinstance(backing, Mapping):
        raise SweeperError("operator is not present in the daily backing snapshot")
    node_id = operator_node_id(operator)
    normalized = normalize_backing_record(
        node_id=node_id,
        record=backing,
        snapshot_date=str(backing.get("snapshotDate", "")),
    )
    if int(normalized["cardSpecificTdh"]) <= 0:
        raise SweeperError("operator has no card-specific TDH backing")
    return {
        "status": "eligible",
        "scheme": "rso-operator-backing-snapshot",
        "tdhBoundary": "daily",
        "snapshotDate": normalized["snapshotDate"],
        "nodeId": node_id,
        "operatorAttester": attester,
        "cardSpecificTdh": normalized["cardSpecificTdh"],
        "backerCount": normalized["backerCount"],
        "rank": normalized["rank"],
        "checkedAt": utc_now(),
    }


def validate_uri(attestation: Mapping[str, object], config: SweeperConfig) -> None:
    uri = str(attestation.get("uri", ""))
    if not uri:
        if config.require_uri:
            raise SweeperError("sweeper sponsorship requires a verifiable publication URI")
        return
    doc_block = attestation["docBlock"]
    assert isinstance(doc_block, Mapping)
    expected_content_hash = normalize_bytes32(doc_block["contentHash"])
    try:
        publication = describe_publication_uri(uri)
    except ValueError as exc:
        raise SweeperError(str(exc)) from exc
    locations = publication.get("locations", [])
    if not isinstance(locations, list) or not locations:
        raise SweeperError("publication URI does not contain any locations")
    if len(locations) > config.max_publication_locations:
        raise SweeperError("publication URI contains too many locations")
    expected_bundle_sha256 = str(publication.get("bundleSha256", ""))
    for location in locations:
        bundle_bytes = fetch_uri_bytes(str(location), config)
        if expected_bundle_sha256:
            validate_bundle_sha256(bundle_bytes, expected_bundle_sha256)
        validate_release_bundle(bundle_bytes, expected_content_hash, config)


def validate_bundle_sha256(bundle_bytes: bytes, expected_bundle_sha256: str) -> None:
    expected = expected_bundle_sha256.lower()
    if expected.startswith("0x"):
        expected = expected[2:]
    if len(expected) != 64:
        raise SweeperError("bundle fingerprint must be a SHA-256 value")
    int(expected, 16)
    actual = hashlib.sha256(bundle_bytes).hexdigest()
    if actual != expected:
        raise SweeperError("bundle fingerprint does not match publication locator")


def fetch_uri_bytes(uri: str, config: SweeperConfig) -> bytes:
    for attempt in range(config.fetch_retries + 1):
        try:
            return fetch_uri_bytes_once(uri, config)
        except urllib.error.HTTPError as exc:
            if not http_error_retryable(exc) or attempt == config.fetch_retries:
                raise SweeperError(f"publication URI fetch failed for {uri}: HTTP {exc.code}") from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt == config.fetch_retries:
                raise SweeperError(f"publication URI fetch did not settle for {uri}: {exc}") from exc
        time.sleep(config.fetch_retry_delay)
    raise SweeperError(f"publication URI fetch did not settle for {uri}")


def fetch_uri_bytes_once(uri: str, config: SweeperConfig) -> bytes:
    url = publication_url(uri)
    return fetch_url_bytes_with_redirects(
        url,
        timeout=config.request_timeout,
        max_bytes=config.max_bundle_bytes,
        headers={"user-agent": "rso-sweeper/1"},
        label="publication URI",
        allow_authorized_redirects=False,
    )


def fetch_url_bytes_with_redirects(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    headers: Mapping[str, str],
    label: str,
    allow_authorized_redirects: bool,
) -> bytes:
    opener = urllib.request.build_opener(NoRedirectHandler)
    current = url
    origin_host = urllib.parse.urlparse(url).hostname or ""
    for _redirect in range(4):
        validate_fetch_url(current)
        request_headers = dict(headers)
        if not allow_authorized_redirects and (urllib.parse.urlparse(current).hostname or "") != origin_host:
            request_headers.pop("authorization", None)
            request_headers.pop("Authorization", None)
        request = urllib.request.Request(current, headers=request_headers)
        try:
            with opener.open(request, timeout=timeout) as response:
                return read_limited(response, max_bytes, label=label)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            location = exc.headers.get("location")
            if not location:
                raise SweeperError(f"{label} redirect response is missing Location") from exc
            current = urllib.parse.urljoin(current, location)
    raise SweeperError(f"{label} redirects too many times")


def http_error_retryable(exc: urllib.error.HTTPError) -> bool:
    return exc.code in (408, 425, 429, 500, 502, 503, 504)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def publication_url(uri: str) -> str:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme == "ar" and parsed.netloc:
        return ARWEAVE_GATEWAY + "/" + parsed.netloc + parsed.path
    if parsed.scheme == "https":
        return uri
    raise SweeperError("unsupported publication URI")


def validate_fetch_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise SweeperError("publication URI must resolve to HTTPS")
    host = parsed.hostname
    if not host:
        raise SweeperError("publication URI is missing a host")
    host = host.lower()
    if not host_allowed(host):
        raise SweeperError("publication URI host is not allowed")
    reject_private_host(host)


def host_allowed(host: str) -> bool:
    allowed_suffixes = ("arweave.net", "github.com", "githubusercontent.com")
    return any(host == suffix or host.endswith("." + suffix) for suffix in allowed_suffixes)


def reject_private_host(host: str) -> None:
    try:
        addresses = [host]
        ipaddress.ip_address(host)
    except ValueError:
        try:
            addresses = [item[4][0] for item in socket.getaddrinfo(host, None)]
        except OSError as exc:
            raise SweeperError(f"publication URI host cannot be resolved: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise SweeperError("publication URI resolves to a non-public address")


def validate_release_bundle(bundle_bytes: bytes, expected_content_hash: str, config: SweeperConfig) -> None:
    expected_sha = expected_content_hash[2:]
    allowed = {
        "audit.json",
        "catalog.json.gz",
        "delta.json",
        "manifest.json",
        "release-manifest.json",
        "visibility_state.json",
    }
    seen: dict[str, object] = {}
    catalog_gz = None
    with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r|gz") as tar:
        for member in tar:
            name = member.name
            if name not in allowed:
                raise SweeperError(f"release bundle contains unexpected file: {name}")
            if name in seen:
                raise SweeperError(f"release bundle contains duplicate file: {name}")
            if not member.isfile():
                raise SweeperError(f"release bundle member is not a regular file: {name}")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise SweeperError(f"release bundle cannot read {name}")
            if name == "catalog.json.gz":
                catalog_gz = read_limited(extracted, config.max_catalog_bytes, label=name)
                seen[name] = True
            elif name in ("manifest.json", "release-manifest.json"):
                seen[name] = load_stream_json(extracted, name, config.max_json_bytes)
            else:
                read_limited(extracted, config.max_json_bytes, label=name)
                seen[name] = True
    release_manifest = seen.get("release-manifest.json")
    manifest = seen.get("manifest.json")
    if not isinstance(release_manifest, dict):
        raise SweeperError("release bundle is missing release-manifest.json")
    if not isinstance(manifest, dict):
        raise SweeperError("release bundle is missing manifest.json")
    if release_manifest.get("catalog_sha256") != expected_sha:
        raise SweeperError("release manifest catalog fingerprint does not match attestation")
    if manifest.get("sha256") != expected_sha:
        raise SweeperError("manifest fingerprint does not match attestation")
    if catalog_gz is None:
        raise SweeperError("release bundle is missing catalog.json.gz")
    catalog_bytes = gzip_decompress_limited(catalog_gz, config.max_catalog_bytes)
    if hashlib.sha256(catalog_bytes).hexdigest() != expected_sha:
        raise SweeperError("canonical catalog fingerprint does not match attestation")


def gzip_decompress_limited(payload: bytes, limit: int) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as stream:
        return read_limited(stream, limit)


def load_stream_json(stream, name: str, limit: int) -> dict[str, object]:
    raw = json.loads(read_limited(stream, limit, label=name).decode("utf-8"))
    if not isinstance(raw, dict):
        raise SweeperError(f"{name} must contain a JSON object")
    return raw


def read_limited(stream, limit: int, *, label: str = "download") -> bytes:
    if limit < 1:
        raise SweeperError(f"{label} size limit must be positive")
    chunks = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise SweeperError(f"{label} exceeds sweeper size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def handle_signed_attestation(
    payload: Mapping[str, object],
    *,
    operator: Mapping[str, object],
    config: SweeperConfig,
    rpc: EthereumRpc | None = None,
    run=subprocess.run,
    expected_date: str | None = None,
) -> dict[str, object]:
    signed = extract_signed(payload)
    prepared = signed["prepared"]
    if not isinstance(prepared, Mapping):
        raise SweeperError("signed.prepared must be an object")
    attestation = prepared["attestation"]
    if not isinstance(attestation, Mapping):
        raise SweeperError("prepared.attestation must be an object")
    validate_prepared_context(prepared, config)
    if expected_date is not None:
        validate_expected_date(payload, attestation, expected_date)
    attester = validate_operator(operator, attestation)
    sponsorship = sponsorship_record(operator, attester)
    rpc_client = rpc or EthereumRpc(config.rpc_url, timeout=config.request_timeout)
    validate_uri(attestation, config)
    signature = normalize_hex_bytes(signed["signature"])
    calldata = attest_doc_calldata(attestation, signature)
    try:
        simulation = simulate_attest_doc(rpc_client, config.docchain_address, calldata)
    except RpcError as exc:
        if is_duplicate_error(str(exc)):
            return {"status": "duplicate", "operatorAttester": attester, "sponsorship": sponsorship}
        raise
    if config.dry_run:
        transaction_hash = ""
        status = "simulated"
    else:
        transaction_hash = submit_with_cast(config=config, calldata=calldata, run=run)
        status = "submitted"
    return {
        "status": status,
        "transactionHash": transaction_hash,
        "blockHash": simulation["blockHash"],
        "uriHash": simulation["uriHash"],
        "attestationKey": simulation["attestationKey"],
        "operatorAttester": attester,
        "sponsorship": sponsorship,
    }


def validate_expected_date(
    payload: Mapping[str, object],
    attestation: Mapping[str, object],
    expected_date: str,
) -> None:
    doc_block = attestation["docBlock"]
    if not isinstance(doc_block, Mapping):
        raise SweeperError("docBlock must be an object")
    if doc_ref_to_date(str(doc_block["docRef"])) != expected_date:
        raise SweeperError("signed attestation docRef does not match swept date")
    payload_date = payload.get("date")
    if payload_date is not None and str(payload_date) != expected_date:
        raise SweeperError("signed artifact date does not match swept date")


def is_duplicate_error(message: str) -> bool:
    normalized = message.lower()
    return "duplicateattestation" in normalized or "duplicate attestation" in normalized


def simulate_attest_doc(rpc: EthereumRpc, contract_address: str, calldata: str) -> dict[str, str]:
    result = rpc.call(
        "eth_call",
        [
            {
                "to": normalize_address(contract_address),
                "data": normalize_hex_bytes(calldata),
            },
            "latest",
        ],
    )
    if not isinstance(result, str):
        raise SweeperError("eth_call returned non-string result")
    return parse_attest_doc_return(result)


def parse_attest_doc_return(result: str) -> dict[str, str]:
    body = normalize_hex_bytes(result)[2:]
    if len(body) < 64 * 3:
        raise SweeperError("attestDoc simulation returned too little data")
    return {
        "blockHash": "0x" + body[0:64],
        "uriHash": "0x" + body[64:128],
        "attestationKey": "0x" + body[128:192],
    }


def submit_with_cast(*, config: SweeperConfig, calldata: str, run=subprocess.run) -> str:
    try:
        with cast_wallet_args_from_env(
            private_key_env=config.private_key_env,
            keystore_json_env="RSO_SWEEPER_KEYSTORE_JSON",
            keystore_path_env="RSO_SWEEPER_KEYSTORE",
            account_env="RSO_SWEEPER_ACCOUNT",
            password_env="RSO_SWEEPER_KEYSTORE_PASSWORD",
            password_file_env="RSO_SWEEPER_KEYSTORE_PASSWORD_FILE",
            allow_raw_private_key=False,
        ) as wallet_args:
            command = [
                cast_path(config.cast),
                "send",
                config.docchain_address,
                "--data",
                calldata,
                "--rpc-url",
                config.rpc_url,
                "--confirmations",
                str(config.confirmations),
                *wallet_args,
            ]
            result = run(command, check=True, capture_output=True, text=True)
    except ValueError as exc:
        raise SweeperError(str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise SweeperError(subprocess_error_detail(exc)) from exc
    return transaction_hash_from_cast_output(result.stdout)


def transaction_hash_from_cast_output(output: str) -> str:
    match = re.search(r"transactionHash\s+([0-9a-fA-Fx]{66})", output)
    if match:
        return "0x" + match.group(1)[2:].lower()
    match = re.search(r"0x[0-9a-fA-F]{64}", output)
    if match:
        return "0x" + match.group(0)[2:].lower()
    raise SweeperError("cast send did not return a transaction hash")


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def date_range(start: str, end: str) -> list[str]:
    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end, "%Y-%m-%d").date()
    if end_date < start_date:
        raise SweeperError("--end must be on or after --start")
    days = []
    current = start_date
    while current <= end_date:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def main() -> int:
    try:
        args = parse_args()
        config = config_from_env()
        if not args.backing:
            raise SweeperError("set --backing or RSO_OPERATOR_BACKING_SNAPSHOT")
        operators = load_operator_registry_source(args.operators, config.request_timeout)
        rpc = EthereumRpc(config.rpc_url, timeout=config.request_timeout)
        report = {
            "schema": "rso-sweeper-report-v1",
            "startedAt": utc_now(),
            "operatorSource": args.operators,
            "dates": [],
        }
        for snapshot_date in date_range(args.start, args.end):
            date_report = {"date": snapshot_date, "operators": []}
            report["dates"].append(date_report)
            try:
                backing = load_backing_snapshot(args.backing, snapshot_date, config.request_timeout)
            except FileNotFoundError:
                print(f"{snapshot_date}: missing backing snapshot")
                date_report["status"] = "missing_backing"
                continue
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    print(f"{snapshot_date}: missing backing snapshot")
                    date_report["status"] = "missing_backing"
                    continue
                raise
            candidate_operators, discovery_records = candidate_operator_payloads(
                operators,
                backing,
                snapshot_date=snapshot_date,
                config=config,
            )
            date_report["operators"].extend(discovery_records)
            candidate_operators = [
                operator
                for operator in candidate_operators
                if int(operator["_backing"]["cardSpecificTdh"]) >= args.min_tdh
            ]
            candidate_operators.sort(key=operator_sponsorship_sort_key)
            selected_operators = candidate_operators if args.limit == 0 else candidate_operators[: args.limit]
            if not selected_operators:
                print(f"{snapshot_date}: no backed operators eligible for sponsorship")
                date_report["status"] = "no_eligible_operators"
                continue
            for operator in selected_operators:
                payload = operator.pop("_payload")
                try:
                    result = handle_signed_attestation(
                        payload,
                        operator=operator,
                        config=config,
                        rpc=rpc,
                        expected_date=snapshot_date,
                    )
                    label = operator_label(operator)
                    print(f"{snapshot_date} {label}: {result['status']}")
                    date_report["operators"].append(
                        {
                            "operator": label,
                            "nodeId": operator.get("nodeId", ""),
                            "attester": result.get("operatorAttester"),
                            "status": result["status"],
                            "transactionHash": result.get("transactionHash", ""),
                        }
                    )
                except urllib.error.HTTPError as exc:
                    label = operator_label(operator)
                    status = "missing" if exc.code == 404 else "deferred"
                    print(f"{snapshot_date} {label}: {status} (HTTP {exc.code})")
                    date_report["operators"].append(
                        {"operator": label, "nodeId": operator.get("nodeId", ""), "status": status, "error": f"HTTP {exc.code}"}
                    )
                except (OSError, RpcError, SweeperError, ValueError, subprocess.CalledProcessError) as exc:
                    label = operator_label(operator)
                    print(f"{snapshot_date} {label}: deferred ({exc})")
                    date_report["operators"].append(
                        {"operator": label, "nodeId": operator.get("nodeId", ""), "status": "deferred", "error": str(exc)}
                    )
            date_report["status"] = "checked"
        if args.report:
            report["finishedAt"] = utc_now()
            write_json_report(Path(args.report), report)
            print(f"Sweeper report: {Path(args.report)}")
        if args.report_dir:
            report["finishedAt"] = report.get("finishedAt", utc_now())
            write_date_reports(Path(args.report_dir), report)
            print(f"Sweeper date reports: {Path(args.report_dir)}")
        return 0
    except (OSError, RpcError, SweeperError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"rso_sweeper.py: {exc}", file=sys.stderr)
        return 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep signed RSO DocChain attestations onchain.")
    parser.add_argument(
        "--operators",
        default=os.environ.get("RSO_SWEEPER_OPERATORS", "github-forks:OMPub/RSO"),
        help=(
            "Operator registry JSON path, or github-forks:OWNER/REPO to discover "
            "candidate repositories from the GitHub fork graph."
        ),
    )
    parser.add_argument(
        "--backing",
        default=os.environ.get("RSO_OPERATOR_BACKING_SNAPSHOT"),
        help=(
            "Daily operator backing snapshot path/URL. May include {date}, or "
            "point to a directory containing YYYY-MM-DD.json."
        ),
    )
    parser.add_argument("--start", required=True, help="First archive date, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="Last archive date, YYYY-MM-DD.")
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("RSO_SWEEPER_TOP_OPERATORS", str(DEFAULT_SPONSORSHIP_LIMIT))),
        help="Maximum backed operators to sponsor per day. Use 0 for no cap.",
    )
    parser.add_argument(
        "--min-tdh",
        type=int,
        default=int(os.environ.get("RSO_SWEEPER_MIN_CARD_SPECIFIC_TDH", "1")),
        help="Minimum card-specific TDH required for treasury sponsorship.",
    )
    parser.add_argument(
        "--report",
        default=os.environ.get("RSO_SWEEPER_REPORT", ""),
        help="Optional path for a structured sweeper report JSON file.",
    )
    parser.add_argument(
        "--report-dir",
        default=os.environ.get("RSO_SWEEPER_REPORT_DIR", ""),
        help="Optional directory where per-date public report JSON files are written.",
    )
    return parser.parse_args()


def write_json_report(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_date_reports(report_dir: Path, report: Mapping[str, object]) -> None:
    dates = report.get("dates", [])
    if not isinstance(dates, list):
        raise SweeperError("sweeper report dates must be an array")
    report_dir.mkdir(parents=True, exist_ok=True)
    for date_report in dates:
        if not isinstance(date_report, Mapping):
            raise SweeperError("sweeper report date entries must be JSON objects")
        snapshot_date = str(date_report.get("date", ""))
        if not snapshot_date:
            raise SweeperError("sweeper report date entry is missing date")
        payload = {
            "schema": "rso-sweeper-date-report-v1",
            "operatorSource": report.get("operatorSource", ""),
            "startedAt": report.get("startedAt", ""),
            "finishedAt": report.get("finishedAt", ""),
            **dict(date_report),
        }
        write_json_report(report_dir / f"{snapshot_date}.json", payload)


if __name__ == "__main__":
    raise SystemExit(main())
