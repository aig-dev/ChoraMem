"""Pinned, allowlisted installer for the ANCHOR public development bundle."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.request import Request, urlopen


EXPECTED_ANCHOR_REPOSITORY = "SalesforceAIResearch/AnchorBench"
EXPECTED_ANCHOR_REVISION = "41bd0e20b9524ce484db301ac15dc14121bf06ad"
EXPECTED_ARTIFACT_VERSION = "0.1.0"
EXPECTED_LICENSE = "CC-BY-NC-4.0"

BANK_ITEM_COUNTS = {
    "bard_orin_lyrae__emotional_vulnerability": 4,
    "co_jules_vega__adversarial": 8,
    "hc_nia_okonkwo__clean": 3,
}


@dataclass(frozen=True, slots=True)
class AnchorSourceFile:
    relative_path: str
    blob_sha: str


SOURCE_FILES = (
    AnchorSourceFile("LICENSE.txt", "c657cab45c850057a63b3605897f5195f3c4ac02"),
    AnchorSourceFile(
        "data/CHECKSUMS.sha256", "b15bf6e5acbd964bea63fa4e7d7dc04c07a5daf7"
    ),
    AnchorSourceFile(
        "data/MANIFEST.json", "118a05b51a566fbf6f4019bfb5552114bb52127f"
    ),
    AnchorSourceFile(
        "data/examples/bard_orin_lyrae__emotional_vulnerability/items.jsonl",
        "1f1560a7a09af5a4f38bdfe6037338978c407224",
    ),
    AnchorSourceFile(
        "data/examples/bard_orin_lyrae__emotional_vulnerability/persona_card.json",
        "ae2dcc85c07a6289be5a1c27008e3412fe5b7c58",
    ),
    AnchorSourceFile(
        "data/examples/bard_orin_lyrae__emotional_vulnerability/transcript.jsonl",
        "b68595f4a315ec464e0866323941014711fba458",
    ),
    AnchorSourceFile(
        "data/examples/co_jules_vega__adversarial/items.jsonl",
        "76307eb5fec83e1296026632186d827c0e1094d7",
    ),
    AnchorSourceFile(
        "data/examples/co_jules_vega__adversarial/persona_card.json",
        "28441ab5b88dc676245149f5253111171afb588e",
    ),
    AnchorSourceFile(
        "data/examples/co_jules_vega__adversarial/transcript.jsonl",
        "a0a28c91b23caa88e449e6022d68e8fc60841dc1",
    ),
    AnchorSourceFile(
        "data/examples/hc_nia_okonkwo__clean/items.jsonl",
        "a875939086794ccb97caa85aed474c72ec64121d",
    ),
    AnchorSourceFile(
        "data/examples/hc_nia_okonkwo__clean/persona_card.json",
        "b8cc035eaaa257545aeb37aa50350523bd5f5a7f",
    ),
    AnchorSourceFile(
        "data/examples/hc_nia_okonkwo__clean/transcript.jsonl",
        "b50231e870f8881e62f17957f283f1e1d0ec1964",
    ),
)

_HEX_SHA1 = re.compile(r"[0-9a-f]{40}")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")


def git_blob_sha(payload: bytes) -> str:
    """Return the Git object id for one blob payload."""

    if not isinstance(payload, bytes):
        raise TypeError("Git blob payload must be bytes")
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()  # noqa: S324 - Git identity


def fetch_github_blob(blob_sha: str) -> bytes:
    """Fetch one immutable GitHub blob and verify its returned identity."""

    if _HEX_SHA1.fullmatch(blob_sha) is None:
        raise ValueError("ANCHOR Git blob SHA must be 40 lowercase hex characters")
    url = (
        "https://api.github.com/repos/"
        f"{EXPECTED_ANCHOR_REPOSITORY}/git/blobs/{blob_sha}"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "chorai-memory-core-anchor-installer",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS host
        document = json.load(response)
    if not isinstance(document, Mapping):
        raise ValueError("GitHub blob response must be an object")
    if document.get("sha") != blob_sha or document.get("encoding") != "base64":
        raise ValueError("GitHub blob response identity or encoding drift")
    encoded = document.get("content")
    if not isinstance(encoded, str):
        raise ValueError("GitHub blob response has no content")
    try:
        payload = base64.b64decode("".join(encoded.split()), validate=True)
    except ValueError as error:
        raise ValueError("GitHub blob response contains invalid base64") from error
    if git_blob_sha(payload) != blob_sha:
        raise ValueError("GitHub blob payload does not match its Git blob SHA")
    return payload


def install_anchor_source(
    target: Path,
    *,
    files: Sequence[AnchorSourceFile] = SOURCE_FILES,
    fetch: Callable[[str], bytes] = fetch_github_blob,
) -> Path:
    """Install exactly ``files`` beneath ``target``, atomically per file."""

    root = Path(target).resolve()
    selected = tuple(files)
    if not selected:
        raise ValueError("ANCHOR source allowlist must not be empty")
    paths: set[str] = set()
    blobs: set[str] = set()
    for source_file in selected:
        relative = _safe_relative_path(source_file.relative_path)
        if relative in paths:
            raise ValueError("ANCHOR source allowlist contains a duplicate path")
        if _HEX_SHA1.fullmatch(source_file.blob_sha) is None:
            raise ValueError("ANCHOR source allowlist contains an invalid Git blob SHA")
        if source_file.blob_sha in blobs:
            raise ValueError("ANCHOR source allowlist contains a duplicate Git blob SHA")
        paths.add(relative)
        blobs.add(source_file.blob_sha)

    root.mkdir(parents=True, exist_ok=True)
    for source_file in selected:
        destination = root / source_file.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.parent.resolve().is_relative_to(root):
            raise ValueError("ANCHOR source relative path escapes its target")
        if destination.is_symlink():
            raise ValueError("ANCHOR source destination must not be a symlink")
        if (
            destination.is_file()
            and git_blob_sha(destination.read_bytes()) == source_file.blob_sha
        ):
            continue
        payload = fetch(source_file.blob_sha)
        if not isinstance(payload, bytes):
            raise TypeError("ANCHOR source fetch must return bytes")
        if git_blob_sha(payload) != source_file.blob_sha:
            raise ValueError(
                f"ANCHOR source Git blob mismatch: {source_file.relative_path}"
            )
        temporary = destination.with_name(destination.name + ".part")
        if temporary.is_symlink():
            raise ValueError("ANCHOR source temporary path must not be a symlink")
        temporary.write_bytes(payload)
        temporary.replace(destination)
    return root


def validate_anchor_source(root: Path) -> Mapping[str, object]:
    """Validate an installed bundle without accessing the network."""

    source_root = Path(root).resolve()
    if not source_root.is_dir():
        raise ValueError(f"ANCHOR source directory is missing: {source_root}")
    expected = {item.relative_path: item for item in SOURCE_FILES}
    if len(expected) != len(SOURCE_FILES):
        raise ValueError("ANCHOR source allowlist contains duplicate paths")
    for item in SOURCE_FILES:
        _safe_relative_path(item.relative_path)

    discovered: set[str] = set()
    for path in source_root.rglob("*"):
        if path.is_symlink():
            raise ValueError("ANCHOR source must not contain symlinks")
        if path.is_file():
            discovered.add(path.relative_to(source_root).as_posix())
    missing = set(expected) - discovered
    if missing:
        raise ValueError(f"missing source file: {sorted(missing)[0]}")
    extras = discovered - set(expected)
    if extras:
        raise ValueError(f"unexpected source file: {sorted(extras)[0]}")

    content_sha256: dict[str, str] = {}
    for relative_path, source_file in expected.items():
        payload = (source_root / relative_path).read_bytes()
        if git_blob_sha(payload) != source_file.blob_sha:
            raise ValueError(f"ANCHOR source Git blob drift: {relative_path}")
        content_sha256[relative_path] = hashlib.sha256(payload).hexdigest()

    manifest = _read_object(source_root / "data/MANIFEST.json", "ANCHOR manifest")
    if manifest.get("artifact_version") != EXPECTED_ARTIFACT_VERSION:
        raise ValueError("ANCHOR artifact version drift")
    if manifest.get("license") != EXPECTED_LICENSE:
        raise ValueError("ANCHOR source license drift")
    public = manifest.get("public_development_set")
    if not isinstance(public, Mapping):
        raise ValueError("ANCHOR manifest has no public development set")
    if public.get("total_banks") != 3 or public.get("total_items") != 15:
        raise ValueError("ANCHOR public development set count drift")
    banks = public.get("banks")
    if not isinstance(banks, list):
        raise ValueError("ANCHOR manifest banks must be a list")
    manifest_counts: dict[str, int] = {}
    for bank in banks:
        if not isinstance(bank, Mapping):
            raise ValueError("ANCHOR manifest bank must be an object")
        bank_id = bank.get("bank_id")
        items = bank.get("items")
        if (
            not isinstance(bank_id, str)
            or not bank_id
            or type(items) is not int
            or items < 0
            or bank_id in manifest_counts
        ):
            raise ValueError("ANCHOR manifest bank identity is invalid")
        manifest_counts[bank_id] = items
    if manifest_counts != BANK_ITEM_COUNTS:
        raise ValueError("ANCHOR manifest bank inventory drift")

    checksums = _read_checksums(source_root / "data/CHECKSUMS.sha256")
    actual_counts: dict[str, int] = {}
    for bank_id, expected_count in BANK_ITEM_COUNTS.items():
        base = source_root / "data/examples" / bank_id
        _read_object(base / "persona_card.json", f"ANCHOR {bank_id} persona card")
        _read_jsonl(base / "transcript.jsonl", f"ANCHOR {bank_id} transcript")
        items = _read_jsonl(base / "items.jsonl", f"ANCHOR {bank_id} items")
        actual_counts[bank_id] = len(items)
        if len(items) != expected_count:
            raise ValueError(f"ANCHOR item count drift: {bank_id}")
        for name in ("persona_card.json", "transcript.jsonl", "items.jsonl"):
            checksum_path = f"examples/{bank_id}/{name}"
            expected_digest = checksums.get(checksum_path)
            if expected_digest is None:
                raise ValueError(f"ANCHOR checksum is missing: {checksum_path}")
            actual_digest = content_sha256[f"data/{checksum_path}"]
            if actual_digest != expected_digest:
                raise ValueError(f"ANCHOR SHA-256 drift: {checksum_path}")

    return {
        "repository": EXPECTED_ANCHOR_REPOSITORY,
        "revision": EXPECTED_ANCHOR_REVISION,
        "artifact_version": EXPECTED_ARTIFACT_VERSION,
        "license": EXPECTED_LICENSE,
        "total_banks": len(actual_counts),
        "total_items": sum(actual_counts.values()),
        "bank_items": actual_counts,
        "files": content_sha256,
    }


def _safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("ANCHOR source requires a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("ANCHOR source requires a safe relative path")
    if path.as_posix() != value:
        raise ValueError("ANCHOR source requires a canonical relative path")
    return value


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _read_jsonl(path: Path, label: str) -> list[Mapping[str, Any]]:
    values: list[Mapping[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not valid UTF-8") from error
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise ValueError(f"{label} contains a blank line at {line_number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} contains invalid JSON at {line_number}") from error
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} row {line_number} must be an object")
        values.append(value)
    return values


def _read_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("ANCHOR checksums are not valid UTF-8") from error
    for line_number, line in enumerate(lines, 1):
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or _HEX_SHA256.fullmatch(fields[0]) is None:
            raise ValueError(f"ANCHOR checksum row {line_number} is invalid")
        relative_path = fields[1].strip().removeprefix("*")
        _safe_relative_path(relative_path)
        if relative_path in checksums:
            raise ValueError("ANCHOR checksums contain a duplicate path")
        checksums[relative_path] = fields[0]
    return checksums
