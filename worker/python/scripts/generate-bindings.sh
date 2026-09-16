#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
worker_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
core_dir=$(CDPATH= cd -- "$worker_dir/../.." && pwd)
output_dir=${1:-"$worker_dir/src"}
python_command=${MEMORY_WORKER_PYTHON:-python3}
proto_tmp=$(mktemp -d)

cleanup() {
	rm -rf -- "$proto_tmp"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$proto_tmp/memory_core_worker/v1" "$output_dir"
cp "$core_dir/api/memory/inference/v1/inference.proto" \
	"$proto_tmp/memory_core_worker/v1/inference.proto"

"$python_command" -m grpc_tools.protoc \
	-I "$proto_tmp" \
	--python_out "$output_dir" \
	--grpc_python_out "$output_dir" \
	"$proto_tmp/memory_core_worker/v1/inference.proto"
