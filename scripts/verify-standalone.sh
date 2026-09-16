#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
core_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
export_root=$(mktemp -d)
export_dir="$export_root/memory-core"

cleanup() {
	rm -rf -- "$export_root"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$export_dir"
(
	cd "$core_dir"
	tar \
		--exclude='.git' \
		--exclude='.cache' \
		--exclude='.pytest_cache' \
		--exclude='.venv' \
		--exclude='__pycache__' \
		--exclude='*.egg-info' \
		--exclude='*.py[co]' \
		--exclude='bin/memoryd' \
		--exclude='build' \
		--exclude='dist' \
		--exclude='node_modules' \
		-cf - .
) | (cd "$export_dir" && tar -xf -)

echo "[standalone] running release gate from isolated export"
(
	cd "$export_dir"
	GOWORK=off make release-gate-local
)

echo "[standalone] isolated export release gate passed"
