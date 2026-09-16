from __future__ import annotations

import atexit
import hashlib
import importlib
import json
import os
import subprocess
import sys
import types
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from memory_core import AsyncMemoryClient, memory_pb2

from .cli import ConfigError, OpenAITextModel
from .memoryagentbench import MEMORY_AGENT_BENCH_COMMIT, MemoryAgentBenchAdapter
from .reference import HarnessProfile


CAPABILITY_SCOPE = "retrieval_recollection"
MAB_DATASET_REPOSITORY = "ai-hyz/MemoryAgentBench"
MAB_OFFICIAL_DATA_FILES: Mapping[str, tuple[str, str]] = {
    "Accurate_Retrieval-00000-of-00001.parquet": (
        "Accurate_Retrieval",
        "56c3cd80fb6731a3e53cd1a6be3148f54df60ff2d290ee50e28f8acebf9655c1",
    ),
}
MAB_REQUIRED_ENV = (
    "MEMORY_AGENT_BENCH_ROOT",
    "MEMORY_CORE_ENDPOINT",
    "MEMORY_CORE_TOKEN",
    "MEMORY_EVAL_REF",
    "OPENAI_API_KEY",
)


class Adapter(Protocol):
    def send_message(
        self,
        message: str,
        memorizing: bool = False,
        query_id: object | None = None,
        context_id: object | None = None,
    ) -> object: ...

    def close(self) -> None: ...


AdapterFactory = Callable[[dict[str, object], dict[str, object], str], Adapter]
DatasetFactory = Callable[[str], object]


def create_local_dataset_loader(
    data_file: Path,
    *,
    official_files: Mapping[str, tuple[str, str]] | None = None,
    from_parquet: DatasetFactory | None = None,
) -> Callable[..., object]:
    resolved = data_file.expanduser().resolve()
    registry = MAB_OFFICIAL_DATA_FILES if official_files is None else official_files
    official = registry.get(resolved.name)
    if official is None:
        raise ConfigError(f"unsupported MemoryAgentBench data file: {resolved.name}")
    expected_split, expected_digest = official
    if not resolved.is_file():
        raise ConfigError(f"MemoryAgentBench data file not found: {resolved}")
    with resolved.open("rb") as stream:
        actual_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual_digest != expected_digest:
        raise ConfigError(
            "MemoryAgentBench data file SHA-256 mismatch: "
            f"expected {expected_digest}, found {actual_digest}"
        )

    def load_dataset(
        dataset_name: str,
        *,
        split: str,
        revision: str,
    ) -> object:
        if (
            dataset_name != MAB_DATASET_REPOSITORY
            or split != expected_split
            or revision != "main"
        ):
            raise ConfigError(
                "local MemoryAgentBench data does not match the requested "
                f"dataset/revision/split: {dataset_name}@{revision}:{split}"
            )
        loader = from_parquet or _dataset_from_parquet
        return loader(str(resolved))

    return load_dataset


def create_agent_wrapper(
    *,
    adapter_factory: AdapterFactory,
    evaluation_ref: str,
) -> type:
    if not evaluation_ref.strip():
        raise ValueError("evaluation_ref must be nonblank")

    class MemoryCoreAgentWrapper:
        _active: MemoryCoreAgentWrapper | None = None

        def __init__(
            self,
            agent_config: dict[str, object],
            dataset_config: dict[str, object],
            load_agent_from: str,
        ) -> None:
            if type(self)._active is not None:
                type(self)._active.close()
            self._agent_config = agent_config
            self._dataset_config = dataset_config
            self._save_dir = Path(load_agent_from)
            self._adapter = adapter_factory(
                agent_config,
                dataset_config,
                load_agent_from,
            )
            self._closed = False
            type(self)._active = self

        def send_message(
            self,
            message: str,
            memorizing: bool = False,
            query_id: object | None = None,
            context_id: object | None = None,
        ) -> object:
            if memorizing:
                return self._adapter.send_message(message, memorizing=True)
            return self._adapter.send_message(
                message,
                memorizing=False,
                query_id=query_id,
                context_id=context_id,
            )

        def save_agent(self) -> None:
            self._save_dir.mkdir(parents=True, exist_ok=True)
            self._marker().write_text(evaluation_ref + "\n", encoding="utf-8")

        def load_agent(self) -> None:
            marker = self._marker()
            actual = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
            if actual != evaluation_ref:
                raise RuntimeError(
                    "MemoryAgentBench state belongs to a different evaluation_ref; "
                    "use a clean output/agent directory or restore MEMORY_EVAL_REF"
                )

        def close(self) -> None:
            if self._closed:
                return
            self._adapter.close()
            self._closed = True
            if type(self)._active is self:
                type(self)._active = None

        def _marker(self) -> Path:
            return self._save_dir / ".memory-core-eval-ref"

    def close_active() -> None:
        if MemoryCoreAgentWrapper._active is not None:
            MemoryCoreAgentWrapper._active.close()

    atexit.register(close_active)
    MemoryCoreAgentWrapper.__name__ = "AgentWrapper"
    return MemoryCoreAgentWrapper


def validate_upstream_commit(
    root: Path,
    *,
    read_head: Callable[[Path], str] | None = None,
) -> None:
    head = (read_head or _read_head)(root).strip()
    if head != MEMORY_AGENT_BENCH_COMMIT:
        raise RuntimeError(
            f"MemoryAgentBench must be checked out at {MEMORY_AGENT_BENCH_COMMIT}; "
            f"found {head or 'no commit'}"
        )


def write_run_metadata(path: Path, *, evaluation_ref: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "benchmark": "MemoryAgentBench",
                "benchmark_commit": MEMORY_AGENT_BENCH_COMMIT,
                "capability_scope": CAPABILITY_SCOPE,
                "disposition_causal_loop_evaluated": False,
                "evaluation_ref": evaluation_ref,
                "report_outcome": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def create_live_agent_wrapper(environ: Mapping[str, str]) -> type:
    missing = [key for key in MAB_REQUIRED_ENV if not environ.get(key, "").strip()]
    if missing:
        raise ConfigError("missing required environment: " + ", ".join(missing))
    evaluation_ref = environ["MEMORY_EVAL_REF"].strip()
    endpoint = environ["MEMORY_CORE_ENDPOINT"].strip()
    token = environ["MEMORY_CORE_TOKEN"].strip()
    api_key = environ["OPENAI_API_KEY"].strip()
    base_url = environ.get("OPENAI_BASE_URL", "").strip() or None
    reasoning_effort = (
        environ.get("MEMORY_AGENT_BENCH_REASONING_EFFORT", "").strip() or None
    )
    settle_seconds = _nonnegative_float(
        environ.get("MEMORY_EVAL_SETTLE_SECONDS", "5"),
        "MEMORY_EVAL_SETTLE_SECONDS",
    )

    def adapter_factory(
        agent_config: dict[str, object],
        dataset_config: dict[str, object],
        load_agent_from: str,
    ) -> MemoryAgentBenchAdapter:
        model = _config_text(agent_config, "model")
        sub_dataset = _config_text(dataset_config, "sub_dataset")
        max_output_tokens = _config_positive_int(
            dataset_config,
            "generation_max_length",
        )
        instructions = _official_template(
            sub_dataset,
            "system",
            _config_text(agent_config, "agent_name"),
        )
        tokenizer = _tokenizer(model)
        context_ref = hashlib.sha256(
            str(Path(load_agent_from).resolve()).encode()
        ).hexdigest()[:24]
        profile = HarnessProfile(
            evaluation_ref=evaluation_ref,
            harness="memory-agent-bench",
            harness_version=MEMORY_AGENT_BENCH_COMMIT,
            model=model,
            profile_version="memoryagentbench-v0",
            prompt_version=sub_dataset,
            scenario_set=sub_dataset,
            instructions=instructions,
            tenant_ref=environ.get("MEMORY_EVAL_TENANT_REF", "memory-core-eval").strip(),
            agent_ref=environ.get("MEMORY_EVAL_AGENT_REF", "mab-agent").strip(),
            relationship_ref=environ.get(
                "MEMORY_EVAL_RELATIONSHIP_REF", "mab-relationship"
            ).strip(),
            user_ref=environ.get("MEMORY_EVAL_USER_REF", "mab-user").strip(),
            constitution=memory_pb2.Constitution(),
            max_output_tokens=max_output_tokens,
        )
        return MemoryAgentBenchAdapter(
            memory_factory=lambda: AsyncMemoryClient.connect(
                endpoint,
                token_provider=lambda _tenant_ref: token,
            ),
            model_factory=lambda: OpenAITextModel(
                model=model,
                api_key=api_key,
                base_url=base_url,
                reasoning_effort=reasoning_effort,
            ),
            profile=profile,
            benchmark_context_ref=context_ref,
            count_tokens=lambda text: len(
                tokenizer.encode(text, disallowed_special=())
            ),
            settle_seconds=settle_seconds,
        )

    return create_agent_wrapper(
        adapter_factory=adapter_factory,
        evaluation_ref=evaluation_ref,
    )


def run_upstream(
    root: Path,
    wrapper_type: type,
    *,
    dataset_loader: Callable[..., object] | None = None,
) -> None:
    original_cwd = Path.cwd()
    original_path = list(sys.path)
    previous_agent = sys.modules.get("agent")
    data_utils: types.ModuleType | None = None
    previous_dataset_loader: object | None = None
    stub = types.ModuleType("agent")
    stub.AgentWrapper = wrapper_type
    sys.modules["agent"] = stub
    try:
        os.chdir(root)
        sys.path.insert(0, str(root))
        upstream = importlib.import_module("main")
        if dataset_loader is not None:
            data_utils = importlib.import_module("utils.eval_data_utils")
            previous_dataset_loader = data_utils.load_dataset
            data_utils.load_dataset = dataset_loader
        upstream.main()
    finally:
        if data_utils is not None:
            data_utils.load_dataset = previous_dataset_loader
        os.chdir(original_cwd)
        sys.path[:] = original_path
        if previous_agent is None:
            sys.modules.pop("agent", None)
        else:
            sys.modules["agent"] = previous_agent


def main(environ: Mapping[str, str] | None = None) -> int:
    environment = os.environ if environ is None else environ
    try:
        missing = [
            key for key in MAB_REQUIRED_ENV if not environment.get(key, "").strip()
        ]
        if missing:
            raise ConfigError("missing required environment: " + ", ".join(missing))
        root = Path(environment["MEMORY_AGENT_BENCH_ROOT"]).expanduser().resolve()
        validate_upstream_commit(root)
        evaluation_ref = environment["MEMORY_EVAL_REF"].strip()
        metadata_path = Path(
            environment.get(
                "MEMORY_AGENT_BENCH_METADATA",
                str(root / "outputs" / "memory-core-run-metadata.json"),
            )
        ).expanduser()
        write_run_metadata(metadata_path, evaluation_ref=evaluation_ref)
        data_file = environment.get("MEMORY_AGENT_BENCH_DATA_FILE", "").strip()
        dataset_loader = (
            create_local_dataset_loader(Path(data_file)) if data_file else None
        )
        run_upstream(
            root,
            create_live_agent_wrapper(environment),
            dataset_loader=dataset_loader,
        )
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"MemoryAgentBench failed: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, ConfigError) else 1


def _read_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _dataset_from_parquet(path: str) -> object:
    from datasets import Dataset

    return Dataset.from_parquet(path)


def _official_template(sub_dataset: str, template: str, agent_name: str) -> str:
    from utils.templates import get_template

    return get_template(sub_dataset, template, agent_name)


def _tokenizer(model: str) -> Any:
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.encoding_for_model("gpt-4o-mini")


def _config_text(config: Mapping[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"MemoryAgentBench config {key} must be nonblank text")
    return value.strip()


def _config_positive_int(config: Mapping[str, object], key: str) -> int:
    value = config.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"MemoryAgentBench config {key} must be a positive integer")
    return value


def _nonnegative_float(value: str, name: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be numeric") from exc
    if result < 0:
        raise ConfigError(f"{name} must not be negative")
    return result


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
