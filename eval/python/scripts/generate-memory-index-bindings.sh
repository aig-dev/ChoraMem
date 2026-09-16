#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
core_dir=$(CDPATH= cd -- "$script_dir/../../.." && pwd)
output_dir=${1:-"$core_dir/eval/python/src"}

mkdir -p "$output_dir"
python -m grpc_tools.protoc \
	-I "$core_dir/api" \
	--python_out="$output_dir" \
	--grpc_python_out="$output_dir" \
	"$core_dir/api/memoryindex/v1/memory_index.proto"
