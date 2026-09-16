#!/bin/sh
set -eu

test_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sdk_dir=$(CDPATH= cd -- "$test_dir/.." && pwd)
package_tmp=$(mktemp -d)

cleanup() {
	rm -rf -- "$package_tmp"
}
trap cleanup EXIT HUP INT TERM

(
	cd "$sdk_dir"
	npm run build
	npm pack --silent --pack-destination "$package_tmp" >/dev/null
)

tar -xzf "$package_tmp/"*.tgz -C "$package_tmp"
packaged_example="$package_tmp/package/examples/generic-lifecycle.ts"

if ! grep -Fq 'from "@chorai/memory-core";' "$packaged_example"; then
	echo "packaged generic example must import the public @chorai/memory-core entry" >&2
	exit 1
fi
if grep -Fq '../src/' "$packaged_example"; then
	echo "packaged generic example must not import unpackaged source files" >&2
	exit 1
fi

ln -s "$sdk_dir/node_modules" "$package_tmp/package/node_modules"
"$sdk_dir/node_modules/.bin/tsc" \
	--noEmit \
	--target ES2022 \
	--module NodeNext \
	--moduleResolution NodeNext \
	--strict \
	--skipLibCheck \
	--verbatimModuleSyntax \
	"$packaged_example"
