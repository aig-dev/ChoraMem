#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
core_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
benchmark_commit=fe1735de8cf8b9908e1e3d3b5612afc815698062
benchmark_url=https://github.com/HUST-AI-HYZ/MemoryAgentBench.git
benchmark_root=${MEMORY_AGENT_BENCH_ROOT:-"$core_dir/.cache/MemoryAgentBench"}

if [ -z "${MEMORY_CORE_ENDPOINT:-}" ]; then
	echo "MEMORY_CORE_ENDPOINT is required" >&2
	exit 1
fi
if [ -z "${MEMORY_CORE_TOKEN:-}" ]; then
	echo "MEMORY_CORE_TOKEN is required" >&2
	exit 1
fi
if [ -z "${MEMORY_EVAL_REF:-}" ]; then
	echo "MEMORY_EVAL_REF is required so benchmark state can be resumed safely" >&2
	exit 1
fi
if [ -z "${OPENAI_API_KEY:-}" ]; then
	echo "OPENAI_API_KEY is required" >&2
	exit 1
fi

if [ ! -d "$benchmark_root/.git" ]; then
	if [ -e "$benchmark_root" ]; then
		echo "MemoryAgentBench path exists but is not a Git checkout: $benchmark_root" >&2
		exit 1
	fi
	git clone "$benchmark_url" "$benchmark_root"
	git -C "$benchmark_root" checkout --detach "$benchmark_commit"
fi

actual_commit=$(git -C "$benchmark_root" rev-parse HEAD)
if [ "$actual_commit" != "$benchmark_commit" ]; then
	echo "MemoryAgentBench must be at $benchmark_commit; found $actual_commit" >&2
	exit 1
fi

export MEMORY_AGENT_BENCH_ROOT="$benchmark_root"
export PYTHONPATH="$core_dir/sdk/python/src:$core_dir/eval/python/src${PYTHONPATH:+:$PYTHONPATH}"

if [ "$#" -eq 0 ]; then
	set -- \
		--agent_config "$core_dir/eval/memoryagentbench/agent_config.yaml" \
		--dataset_config "$benchmark_root/configs/data_conf/Accurate_Retrieval/EventQA/Eventqa_64k.yaml" \
		--max_test_queries_ablation 1
fi

cd "$benchmark_root"
python3 -m memory_core_eval.memoryagentbench_runner "$@"
