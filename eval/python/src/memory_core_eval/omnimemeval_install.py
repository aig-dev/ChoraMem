"""Install the Memory Core bridge into one pinned OmniMemEval checkout."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


EXPECTED_OMNIMEMEVAL_REVISION = "0b1ea8d28aa2d3e03ac4a6aee17b3006a131da7d"
_BRIDGE = "from memory_core_eval.omnimemeval_adapter import MemoryCoreClient\n"
_REGISTRY_ENTRY = '    "memory_core": ("memory_core_client", "MemoryCoreClient"),\n'
_SEARCH_ENTRY = '    "memory_core": generic_text_search,\n'


def _insert_after(text: str, marker: str, entry: str, label: str) -> str:
    if entry.strip() in text:
        return text
    if text.count(marker) != 1:
        raise ValueError(f"unsupported OmniMemEval {label} structure")
    return text.replace(marker, marker + entry, 1)


def install(
    root: Path,
    *,
    revision: str,
    expected_revision: str = EXPECTED_OMNIMEMEVAL_REVISION,
) -> bool:
    root = root.resolve()
    if revision != expected_revision:
        raise ValueError(
            f"OmniMemEval revision {revision} is unsupported; expected {expected_revision}"
        )
    registry_path = root / "scripts/client_factory/registry.py"
    search_path = root / "scripts/utils/search_helpers.py"
    if not registry_path.is_file() or not search_path.is_file():
        raise ValueError("unsupported OmniMemEval checkout structure")

    registry = _insert_after(
        registry_path.read_text(),
        "_LIB_CLIENT_REGISTRY = {\n",
        _REGISTRY_ENTRY,
        "client registry",
    )
    search = _insert_after(
        search_path.read_text(),
        "DEFAULT_SEARCH_DISPATCH = {\n",
        _SEARCH_ENTRY,
        "search registry",
    )
    bridge_path = root / "scripts/client_factory/memory_core_client.py"
    before = (
        registry_path.read_text(),
        search_path.read_text(),
        bridge_path.read_text() if bridge_path.is_file() else None,
    )
    after = (registry, search, _BRIDGE)
    if before == after:
        return False

    registry_path.write_text(registry)
    search_path.write_text(search)
    bridge_path.write_text(_BRIDGE)
    return True


def current_revision(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("OmniMemEval checkout must be a Git repository") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args(argv)
    changed = install(args.checkout, revision=current_revision(args.checkout))
    print("installed" if changed else "already installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
