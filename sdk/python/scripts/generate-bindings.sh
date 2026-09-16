#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sdk_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
core_dir=$(CDPATH= cd -- "$sdk_dir/../.." && pwd)
check=false
if [ "${1:-}" = "--check" ]; then
	check=true
	output_dir=$(mktemp -d)
else
	output_dir=${1:-"$sdk_dir/src"}
fi
python_command=${MEMORY_SDK_PYTHON:-python3}
python_proto_tmp=$(mktemp -d)

cleanup() {
	rm -rf -- "$python_proto_tmp"
	if [ "$check" = true ]; then rm -rf -- "$output_dir"; fi
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$python_proto_tmp/memory_core/v1" "$output_dir"
cp "$core_dir/api/memory/v1/memory.proto" \
	"$python_proto_tmp/memory_core/v1/memory.proto"

"$python_command" -m grpc_tools.protoc \
	-I "$python_proto_tmp" \
	--python_out "$output_dir" \
	--grpc_python_out "$output_dir" \
	"$python_proto_tmp/memory_core/v1/memory.proto"

if [ "$check" = true ]; then
	diff -u "$sdk_dir/src/memory_core/v1/memory_pb2.py" "$output_dir/memory_core/v1/memory_pb2.py"
	diff -u "$sdk_dir/src/memory_core/v1/memory_pb2_grpc.py" "$output_dir/memory_core/v1/memory_pb2_grpc.py"
fi
