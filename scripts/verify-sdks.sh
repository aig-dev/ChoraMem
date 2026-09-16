#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
core_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
sdk_tmp_dir=$(mktemp -d)

cleanup() {
	rm -rf -- "$sdk_tmp_dir"
}
trap cleanup EXIT HUP INT TERM

echo "[sdk] Go: test and build"
(
	cd "$core_dir"
	go test -count=1 ./sdk/go/...
	go build ./sdk/go/...
)

echo "[sdk] Python: isolated dependencies, generated-binding drift, tests and wheel"
python_stage="$sdk_tmp_dir/python-package"
mkdir -p "$python_stage"
(
	cd "$core_dir/sdk/python"
	tar \
		--exclude='__pycache__' \
		--exclude='*.py[co]' \
		--exclude='.pytest_cache' \
		--exclude='*.egg-info' \
		--exclude='.venv' \
		--exclude='build' \
		--exclude='dist' \
		-cf - README.md pyproject.toml src tests examples
) | (cd "$python_stage" && tar -xf -)
cp "$core_dir/LICENSE" "$python_stage/LICENSE"

python3 -m venv "$sdk_tmp_dir/python-venv"
PATH="$sdk_tmp_dir/python-venv/bin:$PATH"
export PATH PYTHONDONTWRITEBYTECODE=1
python -m pip install --disable-pip-version-check --quiet "$python_stage[test,codegen]"

python_generated="$sdk_tmp_dir/python-generated"
"$core_dir/sdk/python/scripts/generate-bindings.sh" "$python_generated"
diff -u \
	"$core_dir/sdk/python/src/memory_core/v1/memory_pb2.py" \
	"$python_generated/memory_core/v1/memory_pb2.py"
diff -u \
	"$core_dir/sdk/python/src/memory_core/v1/memory_pb2_grpc.py" \
	"$python_generated/memory_core/v1/memory_pb2_grpc.py"

(
	cd "$python_stage"
	python -m pytest -q -p no:cacheprovider
	python -m compileall -q examples
)
mkdir -p "$sdk_tmp_dir/python-wheel"
python -m pip wheel --disable-pip-version-check --quiet --no-deps \
	--wheel-dir "$sdk_tmp_dir/python-wheel" "$python_stage"
python - "$sdk_tmp_dir/python-wheel" "$core_dir/LICENSE" <<'PY'
from pathlib import Path
import sys
import zipfile

wheels = list(Path(sys.argv[1]).glob("*.whl"))
if len(wheels) != 1:
    raise SystemExit(f"expected one wheel, found {len(wheels)}")
with zipfile.ZipFile(wheels[0]) as archive:
    bad = [
        name
        for name in archive.namelist()
        if "__pycache__" in name
        or name.endswith((".pyc", ".pyo"))
        or "/build/" in name
        or "/dist/" in name
    ]
    license_files = [name for name in archive.namelist() if name.endswith("/licenses/LICENSE")]
    if len(license_files) != 1:
        raise SystemExit(f"wheel must contain one LICENSE, found {license_files}")
    packaged_license = archive.read(license_files[0])
if bad:
    raise SystemExit(f"wheel contains build/cache artifacts: {bad}")
if packaged_license != Path(sys.argv[2]).read_bytes():
    raise SystemExit("wheel LICENSE differs from the repository LICENSE")
PY

echo "[sdk] TypeScript: clean install, generated-binding drift, tests, build and package"
(
	cd "$core_dir/sdk/typescript"
	npm ci --ignore-scripts
	npm run generate:check
)

typescript_stage="$sdk_tmp_dir/typescript-package"
mkdir -p "$typescript_stage"
(
	cd "$core_dir/sdk/typescript"
	tar \
		--exclude='node_modules' \
		--exclude='.vite' \
		--exclude='dist' \
		-cf - README.md package.json package-lock.json tsconfig.json tsconfig.build.json src test examples
) | (cd "$typescript_stage" && tar -xf -)
cp "$core_dir/LICENSE" "$typescript_stage/LICENSE"
ln -s "$core_dir/sdk/typescript/node_modules" "$typescript_stage/node_modules"
(
	cd "$typescript_stage"
	npm run typecheck
	npm test
	npm run build
)
mkdir -p "$sdk_tmp_dir/npm-package"
npm pack --silent --pack-destination "$sdk_tmp_dir/npm-package" "$typescript_stage" >/dev/null
if tar -tf "$sdk_tmp_dir/npm-package/"*.tgz | grep -Eq '(^|/)(node_modules|__pycache__|\.pytest_cache|\.vite)(/|$)|\.py[co]$'; then
	echo "npm package contains cache or dependency artifacts" >&2
	exit 1
fi
tar -xOf "$sdk_tmp_dir/npm-package/"*.tgz package/LICENSE | diff -u "$core_dir/LICENSE" -

echo "[sdk] all release checks passed"
