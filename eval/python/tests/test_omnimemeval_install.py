from __future__ import annotations

from pathlib import Path

import pytest


REGISTRY = '''\
_LIB_CLIENT_REGISTRY = {
    "memos": ("memos_client", "MemosClient"),
}

SUPPORTED_LIBS = list(_LIB_CLIENT_REGISTRY.keys())
'''

SEARCH_HELPERS = '''\
DEFAULT_SEARCH_DISPATCH = {
    "memos": generic_text_search,
}
'''


def fake_checkout(root: Path) -> None:
    factory = root / "scripts/client_factory"
    utils = root / "scripts/utils"
    env = root / "env_examples"
    factory.mkdir(parents=True)
    utils.mkdir(parents=True)
    env.mkdir(parents=True)
    (factory / "registry.py").write_text(REGISTRY)
    (utils / "search_helpers.py").write_text(SEARCH_HELPERS)


def test_install_registers_bridge_and_generic_search_exactly_once(tmp_path):
    from memory_core_eval.omnimemeval_install import install

    fake_checkout(tmp_path)
    changed = install(tmp_path, revision="expected", expected_revision="expected")
    snapshot = {
        path.relative_to(tmp_path): path.read_text()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    unchanged = install(tmp_path, revision="expected", expected_revision="expected")

    assert changed is True
    assert unchanged is False
    assert snapshot == {
        path.relative_to(tmp_path): path.read_text()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    assert '"memory_core": ("memory_core_client", "MemoryCoreClient")' in snapshot[
        Path("scripts/client_factory/registry.py")]
    assert '"memory_core": generic_text_search' in snapshot[
        Path("scripts/utils/search_helpers.py")]
    assert snapshot[Path("scripts/client_factory/memory_core_client.py")] == (
        "from memory_core_eval.omnimemeval_adapter import MemoryCoreClient\n"
    )


def test_install_fails_closed_for_unknown_revision_or_structure(tmp_path):
    from memory_core_eval.omnimemeval_install import install

    fake_checkout(tmp_path)
    with pytest.raises(ValueError, match="revision"):
        install(tmp_path, revision="new", expected_revision="expected")
    assert "memory_core" not in (tmp_path / "scripts/client_factory/registry.py").read_text()

    (tmp_path / "scripts/utils/search_helpers.py").write_text("changed upstream layout\n")
    with pytest.raises(ValueError, match="search registry"):
        install(tmp_path, revision="expected", expected_revision="expected")
    assert "memory_core" not in (tmp_path / "scripts/client_factory/registry.py").read_text()
