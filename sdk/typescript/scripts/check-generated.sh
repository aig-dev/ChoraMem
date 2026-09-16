#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sdk_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
core_dir=$(CDPATH= cd -- "$sdk_dir/../.." && pwd)
typescript_proto_tmp=$(mktemp -d)

cleanup() {
	rm -rf -- "$typescript_proto_tmp"
}
trap cleanup EXIT HUP INT TERM

(
	cd "$core_dir"
	"$sdk_dir/node_modules/.bin/buf" generate api/memory/v1/memory.proto \
		--template "$sdk_dir/buf.gen.yaml" \
		--output "$typescript_proto_tmp"
)

diff -ru \
	"$sdk_dir/src/gen" \
	"$typescript_proto_tmp/sdk/typescript/src/gen"
