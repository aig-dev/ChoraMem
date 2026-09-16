from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memory_core_eval.memoryagentbench import MEMORY_AGENT_BENCH_COMMIT
from memory_core_eval import memoryagentbench_runner
from memory_core_eval.memoryagentbench_runner import (
    CAPABILITY_SCOPE,
    create_agent_wrapper,
    validate_upstream_commit,
    write_run_metadata,
)


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, object | None, object | None]] = []
        self.closed = False

    def send_message(
        self,
        message: str,
        memorizing: bool = False,
        query_id: object | None = None,
        context_id: object | None = None,
    ) -> str | dict[str, object]:
        self.calls.append((message, memorizing, query_id, context_id))
        return "Memorized" if memorizing else {"output": "answer"}

    def close(self) -> None:
        self.closed = True


def test_injected_wrapper_preserves_upstream_constructor_and_message_contract(
    tmp_path: Path,
) -> None:
    adapters: list[FakeAdapter] = []

    def adapter_factory(
        agent_config: dict[str, object],
        dataset_config: dict[str, object],
        load_agent_from: str,
    ) -> FakeAdapter:
        assert agent_config["agent_name"] == "rag_memory_core"
        assert dataset_config["sub_dataset"] == "eventqa_65536"
        assert load_agent_from.endswith("exp_0")
        adapter = FakeAdapter()
        adapters.append(adapter)
        return adapter

    wrapper_type = create_agent_wrapper(
        adapter_factory=adapter_factory,
        evaluation_ref="mab-run-1",
    )
    save_dir = tmp_path / "agents" / "exp_0"
    wrapper = wrapper_type(
        {"agent_name": "rag_memory_core"},
        {"sub_dataset": "eventqa_65536"},
        str(save_dir),
    )

    assert wrapper.send_message("memory chunk", memorizing=True) == "Memorized"
    assert wrapper.send_message(
        "what next?", query_id=7, context_id=0
    ) == {"output": "answer"}
    assert adapters[0].calls == [
        ("memory chunk", True, None, None),
        ("what next?", False, 7, 0),
    ]

    wrapper.save_agent()
    assert (save_dir / ".memory-core-eval-ref").read_text() == "mab-run-1\n"
    wrapper.load_agent()
    wrapper.close()
    assert adapters[0].closed is True


def test_injected_wrapper_rejects_state_from_another_evaluation(tmp_path: Path) -> None:
    save_dir = tmp_path / "exp_0"
    save_dir.mkdir()
    (save_dir / ".memory-core-eval-ref").write_text("old-run\n")
    wrapper_type = create_agent_wrapper(
        adapter_factory=lambda *_: FakeAdapter(),
        evaluation_ref="new-run",
    )
    wrapper = wrapper_type({}, {}, str(save_dir))

    with pytest.raises(RuntimeError, match="different evaluation_ref"):
        wrapper.load_agent()


def test_new_context_closes_the_previous_core_adapter(tmp_path: Path) -> None:
    adapters: list[FakeAdapter] = []

    def factory(*_args: object) -> FakeAdapter:
        adapter = FakeAdapter()
        adapters.append(adapter)
        return adapter

    wrapper_type = create_agent_wrapper(
        adapter_factory=factory,
        evaluation_ref="run",
    )

    wrapper_type({}, {}, str(tmp_path / "exp_0"))
    second = wrapper_type({}, {}, str(tmp_path / "exp_1"))

    assert adapters[0].closed is True
    assert adapters[1].closed is False
    second.close()


def test_upstream_commit_is_a_hard_gate(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=MEMORY_AGENT_BENCH_COMMIT):
        validate_upstream_commit(
            tmp_path,
            read_head=lambda _root: "wrong-commit",
        )


def test_metadata_limits_the_claim_to_retrieval_and_recollection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory-core-metadata.json"

    write_run_metadata(path, evaluation_ref="mab-run-1")

    assert json.loads(path.read_text()) == {
        "benchmark": "MemoryAgentBench",
        "benchmark_commit": MEMORY_AGENT_BENCH_COMMIT,
        "capability_scope": CAPABILITY_SCOPE,
        "disposition_causal_loop_evaluated": False,
        "evaluation_ref": "mab-run-1",
        "report_outcome": False,
    }


def test_local_dataset_loader_supplies_verified_parquet_to_official_split(
    tmp_path: Path,
) -> None:
    data_file = tmp_path / "Accurate_Retrieval-00000-of-00001.parquet"
    data_file.write_bytes(b"verified benchmark fixture")
    digest = hashlib.sha256(data_file.read_bytes()).hexdigest()
    loaded_paths: list[str] = []
    expected_dataset = object()

    def from_parquet(path: str) -> object:
        loaded_paths.append(path)
        return expected_dataset

    loader = memoryagentbench_runner.create_local_dataset_loader(
        data_file,
        official_files={data_file.name: ("Accurate_Retrieval", digest)},
        from_parquet=from_parquet,
    )

    actual = loader(
        "ai-hyz/MemoryAgentBench",
        split="Accurate_Retrieval",
        revision="main",
    )

    assert actual is expected_dataset
    assert loaded_paths == [str(data_file.resolve())]


def test_local_dataset_loader_rejects_unverified_data(tmp_path: Path) -> None:
    data_file = tmp_path / "Accurate_Retrieval-00000-of-00001.parquet"
    data_file.write_bytes(b"wrong benchmark data")

    with pytest.raises(memoryagentbench_runner.ConfigError, match="SHA-256 mismatch"):
        memoryagentbench_runner.create_local_dataset_loader(
            data_file,
            official_files={data_file.name: ("Accurate_Retrieval", "0" * 64)},
            from_parquet=lambda _path: object(),
        )


def test_run_upstream_injects_local_dataset_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    utils_dir = tmp_path / "utils"
    utils_dir.mkdir()
    (utils_dir / "__init__.py").write_text("", encoding="utf-8")
    (utils_dir / "eval_data_utils.py").write_text(
        "def load_dataset(*_args, **_kwargs):\n"
        "    return 'network'\n"
        "\n"
        "def load_eval_data():\n"
        "    return load_dataset(\n"
        "        'ai-hyz/MemoryAgentBench',\n"
        "        split='Accurate_Retrieval',\n"
        "        revision='main',\n"
        "    )\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from utils.eval_data_utils import load_eval_data\n"
        "\n"
        "def main():\n"
        "    Path(os.environ['MAB_TEST_RESULT']).write_text(load_eval_data())\n",
        encoding="utf-8",
    )
    result_path = tmp_path / "result.txt"
    monkeypatch.setenv("MAB_TEST_RESULT", str(result_path))
    module_names = ("main", "utils", "utils.eval_data_utils")
    previous_modules = {name: sys.modules.pop(name, None) for name in module_names}

    try:
        memoryagentbench_runner.run_upstream(
            tmp_path,
            object,
            dataset_loader=lambda *_args, **_kwargs: "local",
        )
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
            previous = previous_modules[name]
            if previous is not None:
                sys.modules[name] = previous

    assert result_path.read_text(encoding="utf-8") == "local"


def test_main_uses_verified_local_dataset_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    utils_dir = tmp_path / "utils"
    utils_dir.mkdir()
    (utils_dir / "__init__.py").write_text("", encoding="utf-8")
    (utils_dir / "eval_data_utils.py").write_text(
        "def load_dataset(*_args, **_kwargs):\n"
        "    return 'network'\n"
        "\n"
        "def load_eval_data():\n"
        "    return load_dataset(\n"
        "        'ai-hyz/MemoryAgentBench',\n"
        "        split='Accurate_Retrieval',\n"
        "        revision='main',\n"
        "    )\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from utils.eval_data_utils import load_eval_data\n"
        "\n"
        "def main():\n"
        "    Path(os.environ['MAB_TEST_RESULT']).write_text(load_eval_data())\n",
        encoding="utf-8",
    )
    result_path = tmp_path / "result.txt"
    data_file = tmp_path / "Accurate_Retrieval-00000-of-00001.parquet"
    data_file.write_bytes(b"verified benchmark fixture")
    digest = hashlib.sha256(data_file.read_bytes()).hexdigest()
    monkeypatch.setenv("MAB_TEST_RESULT", str(result_path))
    monkeypatch.setattr(memoryagentbench_runner, "validate_upstream_commit", lambda _root: None)
    monkeypatch.setattr(
        memoryagentbench_runner,
        "MAB_OFFICIAL_DATA_FILES",
        {data_file.name: ("Accurate_Retrieval", digest)},
    )
    monkeypatch.setattr(
        memoryagentbench_runner,
        "_dataset_from_parquet",
        lambda _path: "local",
        raising=False,
    )
    module_names = ("main", "utils", "utils.eval_data_utils")
    previous_modules = {name: sys.modules.pop(name, None) for name in module_names}

    try:
        result = memoryagentbench_runner.main(
            {
                "MEMORY_AGENT_BENCH_ROOT": str(tmp_path),
                "MEMORY_AGENT_BENCH_DATA_FILE": str(data_file),
                "MEMORY_AGENT_BENCH_METADATA": str(tmp_path / "metadata.json"),
                "MEMORY_CORE_ENDPOINT": "http://127.0.0.1:18081",
                "MEMORY_CORE_TOKEN": "test-token",
                "MEMORY_EVAL_REF": "mab-local-data",
                "OPENAI_API_KEY": "test-key",
            }
        )
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
            previous = previous_modules[name]
            if previous is not None:
                sys.modules[name] = previous

    assert result == 0
    assert result_path.read_text(encoding="utf-8") == "local"


def test_live_wrapper_passes_explicit_reasoning_effort_to_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_options: list[dict[str, object]] = []

    class FakeModel:
        def __init__(self, **options: object) -> None:
            model_options.append(options)

    class FakeLiveAdapter(FakeAdapter):
        def __init__(self, *, model_factory, **_options: object) -> None:
            super().__init__()
            self.model = model_factory()

    monkeypatch.setattr(memoryagentbench_runner, "OpenAITextModel", FakeModel)
    monkeypatch.setattr(
        memoryagentbench_runner,
        "MemoryAgentBenchAdapter",
        FakeLiveAdapter,
    )
    monkeypatch.setattr(
        memoryagentbench_runner,
        "_official_template",
        lambda *_args: "Answer directly.",
    )
    monkeypatch.setattr(
        memoryagentbench_runner,
        "_tokenizer",
        lambda _model: object(),
    )
    wrapper_type = memoryagentbench_runner.create_live_agent_wrapper(
        {
            "MEMORY_AGENT_BENCH_ROOT": str(tmp_path),
            "MEMORY_CORE_ENDPOINT": "http://127.0.0.1:18081",
            "MEMORY_CORE_TOKEN": "test-token",
            "MEMORY_EVAL_REF": "mab-reasoning",
            "MEMORY_AGENT_BENCH_REASONING_EFFORT": "none",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://example.invalid/v1",
        }
    )

    wrapper = wrapper_type(
        {"agent_name": "rag_memory_core", "model": "reasoning-model"},
        {"sub_dataset": "eventqa_65536", "generation_max_length": 40},
        str(tmp_path / "agent"),
    )
    wrapper.close()

    assert model_options == [
        {
            "model": "reasoning-model",
            "api_key": "test-key",
            "base_url": "https://example.invalid/v1",
            "reasoning_effort": "none",
        }
    ]


def test_launcher_validates_core_configuration_before_touching_upstream(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "git-was-called"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        f"#!/bin/sh\n: > '{marker}'\nexit 73\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    script = Path(__file__).resolve().parents[3] / "scripts/run-memoryagentbench.sh"

    result = subprocess.run(
        ["sh", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "MEMORY_AGENT_BENCH_ROOT": str(tmp_path / "checkout"),
        },
    )

    assert result.returncode != 0
    assert "MEMORY_CORE_ENDPOINT is required" in result.stderr
    assert marker.exists() is False
