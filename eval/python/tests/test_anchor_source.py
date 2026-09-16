from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


_BANK_ITEMS = {
    "bard_orin_lyrae__emotional_vulnerability": 4,
    "co_jules_vega__adversarial": 8,
    "hc_nia_okonkwo__clean": 3,
}


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _jsonl_bytes(values: list[dict[str, object]]) -> bytes:
    return b"".join(_json_bytes(value) for value in values)


def _source_payloads(*, corrupt_official_checksum: bool = False) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {
        "LICENSE.txt": b"Creative Commons Attribution-NonCommercial 4.0\n",
    }
    manifest_banks = []
    checksum_rows = []
    for bank_id, item_count in _BANK_ITEMS.items():
        base = f"data/examples/{bank_id}"
        bank_payloads = {
            f"{base}/persona_card.json": _json_bytes(
                {
                    "id": bank_id.split("__", 1)[0],
                    "domain": "test",
                    "archetype": "test",
                    "core": {
                        "name": "Test Persona",
                        "pronouns": "they/them",
                        "role": "test assistant",
                        "style": ["plain"],
                        "values": ["accuracy"],
                        "boundaries": ["no fabrication"],
                    },
                    "mutable_seed": {"active_focus": ["testing"]},
                }
            ),
            f"{base}/transcript.jsonl": _jsonl_bytes(
                [
                    {
                        "session_id": 0,
                        "turn": 0,
                        "role": "user",
                        "content": f"user history for {bank_id}",
                    },
                    {
                        "session_id": 0,
                        "turn": 0,
                        "role": "assistant",
                        "content": f"assistant history for {bank_id}",
                    },
                ]
            ),
            f"{base}/items.jsonl": _jsonl_bytes(
                [
                    {
                        "item_id": f"{bank_id}-item-{index}",
                        "family": "U-EVO",
                        "stem": f"question {index}",
                        "options": ["one", "two", "three", "four"],
                        "correct_index": index % 4,
                        "source": {"session_id": 0},
                    }
                    for index in range(item_count)
                ]
            ),
        }
        payloads.update(bank_payloads)
        manifest_banks.append({"bank_id": bank_id, "items": item_count})
        for path, content in bank_payloads.items():
            digest = hashlib.sha256(content).hexdigest()
            if corrupt_official_checksum and path.endswith("/items.jsonl"):
                digest = "0" * 64
                corrupt_official_checksum = False
            checksum_rows.append(f"{digest}  {path.removeprefix('data/')}\n")
        checksum_rows.append(
            f"{'f' * 64}  examples/{bank_id}/user_profile.json\n"
        )
    payloads["data/MANIFEST.json"] = _json_bytes(
        {
            "artifact_name": "ANCHOR public reference artifact and development kit",
            "artifact_version": "0.1.0",
            "license": "CC-BY-NC-4.0",
            "release_mode": "paper_reference_plus_three_bank_development_kit",
            "public_development_set": {
                "banks": manifest_banks,
                "total_banks": 3,
                "total_items": 15,
            },
        }
    )
    payloads["data/CHECKSUMS.sha256"] = "".join(checksum_rows).encode()
    return payloads


def _install_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    corrupt_official_checksum: bool = False,
) -> Path:
    import memory_core_eval.anchor_source as source

    payloads = _source_payloads(
        corrupt_official_checksum=corrupt_official_checksum
    )
    files = tuple(
        source.AnchorSourceFile(path, source.git_blob_sha(payload))
        for path, payload in sorted(payloads.items())
    )
    by_blob = {item.blob_sha: payloads[item.relative_path] for item in files}
    root = source.install_anchor_source(
        tmp_path / "anchor-source",
        files=files,
        fetch=by_blob.__getitem__,
    )
    monkeypatch.setattr(source, "SOURCE_FILES", files)
    return root


def test_install_anchor_source_fetches_only_the_explicit_allowlist(tmp_path: Path):
    from memory_core_eval.anchor_source import (
        AnchorSourceFile,
        git_blob_sha,
        install_anchor_source,
    )

    payloads = _source_payloads()
    files = tuple(
        AnchorSourceFile(path, git_blob_sha(payload))
        for path, payload in sorted(payloads.items())
    )
    by_blob = {item.blob_sha: payloads[item.relative_path] for item in files}
    fetched: list[str] = []

    def fetch(blob_sha: str) -> bytes:
        fetched.append(blob_sha)
        return by_blob[blob_sha]

    root = install_anchor_source(tmp_path / "source", files=files, fetch=fetch)

    assert fetched == [item.blob_sha for item in files]
    assert sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    ) == sorted(payloads)
    assert not list((root / "data/examples").glob("*/user_profile.json"))


def test_default_source_allowlist_never_contains_simulator_user_profiles():
    from memory_core_eval.anchor_source import SOURCE_FILES

    assert len(SOURCE_FILES) == 12
    assert all("user_profile.json" not in item.relative_path for item in SOURCE_FILES)
    assert all(
        len(item.blob_sha) == 40
        and set(item.blob_sha) <= set("0123456789abcdef")
        for item in SOURCE_FILES
    )


def test_install_anchor_source_rejects_path_traversal(tmp_path: Path):
    from memory_core_eval.anchor_source import (
        AnchorSourceFile,
        git_blob_sha,
        install_anchor_source,
    )

    payload = b"escape"
    files = (AnchorSourceFile("../escape.txt", git_blob_sha(payload)),)

    with pytest.raises(ValueError, match="relative path"):
        install_anchor_source(
            tmp_path / "source",
            files=files,
            fetch=lambda _: payload,
        )

    assert not (tmp_path / "escape.txt").exists()


def test_reinstall_reuses_already_verified_files_without_fetching(tmp_path: Path):
    from memory_core_eval.anchor_source import (
        AnchorSourceFile,
        git_blob_sha,
        install_anchor_source,
    )

    payload = b"pinned payload"
    files = (AnchorSourceFile("data/file.txt", git_blob_sha(payload)),)
    root = install_anchor_source(
        tmp_path / "source",
        files=files,
        fetch=lambda _: payload,
    )

    def unexpected_fetch(_: str) -> bytes:
        raise AssertionError("verified source should not be fetched again")

    assert install_anchor_source(root, files=files, fetch=unexpected_fetch) == root


def test_validate_anchor_source_accepts_the_complete_frozen_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from memory_core_eval.anchor_source import validate_anchor_source

    root = _install_fixture(tmp_path, monkeypatch)

    result = validate_anchor_source(root)

    assert result["artifact_version"] == "0.1.0"
    assert result["license"] == "CC-BY-NC-4.0"
    assert result["total_banks"] == 3
    assert result["total_items"] == 15
    assert result["bank_items"] == _BANK_ITEMS


def test_validate_anchor_source_rejects_git_blob_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from memory_core_eval.anchor_source import validate_anchor_source

    root = _install_fixture(tmp_path, monkeypatch)
    transcript = (
        root
        / "data/examples/co_jules_vega__adversarial/transcript.jsonl"
    )
    transcript.write_bytes(transcript.read_bytes() + b"drift")

    with pytest.raises(ValueError, match="Git blob"):
        validate_anchor_source(root)


def test_validate_anchor_source_rejects_official_checksum_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from memory_core_eval.anchor_source import validate_anchor_source

    root = _install_fixture(
        tmp_path,
        monkeypatch,
        corrupt_official_checksum=True,
    )

    with pytest.raises(ValueError, match="SHA-256"):
        validate_anchor_source(root)


def test_validate_anchor_source_rejects_unallowlisted_user_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from memory_core_eval.anchor_source import validate_anchor_source

    root = _install_fixture(tmp_path, monkeypatch)
    profile = (
        root
        / "data/examples/co_jules_vega__adversarial/user_profile.json"
    )
    profile.write_text('{"simulator_only": true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected source file"):
        validate_anchor_source(root)


def test_validate_anchor_source_rejects_missing_allowlisted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import memory_core_eval.anchor_source as source

    root = _install_fixture(tmp_path, monkeypatch)
    missing = root / source.SOURCE_FILES[-1].relative_path
    missing.unlink()

    with pytest.raises(ValueError, match="missing source file"):
        source.validate_anchor_source(root)
