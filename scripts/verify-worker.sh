#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
core_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
worker_tmp_dir=$(mktemp -d)
worker_pid=""

cleanup() {
	if [ -n "$worker_pid" ]; then
		kill "$worker_pid" >/dev/null 2>&1 || true
		wait "$worker_pid" >/dev/null 2>&1 || true
	fi
	rm -rf -- "$worker_tmp_dir"
}
trap cleanup EXIT HUP INT TERM

worker_stage="$worker_tmp_dir/package"
mkdir -p "$worker_stage"
(
	cd "$core_dir/worker/python"
	tar \
		--exclude='__pycache__' \
		--exclude='*.py[co]' \
		--exclude='.pytest_cache' \
		--exclude='*.egg-info' \
		--exclude='.venv' \
		--exclude='build' \
		--exclude='dist' \
		-cf - README.md pyproject.toml src tests
) | (cd "$worker_stage" && tar -xf -)
cp "$core_dir/LICENSE" "$worker_stage/LICENSE"

python3 -m venv "$worker_tmp_dir/venv"
PATH="$worker_tmp_dir/venv/bin:$PATH"
export PATH PYTHONDONTWRITEBYTECODE=1
unset OPENAI_API_KEY
python -m pip install --disable-pip-version-check --quiet "$worker_stage[test,codegen]"

generated="$worker_tmp_dir/generated"
"$core_dir/worker/python/scripts/generate-bindings.sh" "$generated"
diff -u \
	"$core_dir/worker/python/src/memory_core_worker/v1/inference_pb2.py" \
	"$generated/memory_core_worker/v1/inference_pb2.py"
diff -u \
	"$core_dir/worker/python/src/memory_core_worker/v1/inference_pb2_grpc.py" \
	"$generated/memory_core_worker/v1/inference_pb2_grpc.py"

(
	cd "$worker_stage"
	python -m pytest -q -p no:cacheprovider
)
mkdir -p "$worker_tmp_dir/wheel"
python -m pip wheel --disable-pip-version-check --quiet --no-deps \
	--wheel-dir "$worker_tmp_dir/wheel" "$worker_stage"
python - "$worker_tmp_dir/wheel" "$core_dir/LICENSE" <<'PY'
from pathlib import Path
import sys
import zipfile

wheels = list(Path(sys.argv[1]).glob("*.whl"))
if len(wheels) != 1:
    raise SystemExit(f"expected one wheel, found {len(wheels)}")
with zipfile.ZipFile(wheels[0]) as archive:
    bad = [name for name in archive.namelist() if "__pycache__" in name or name.endswith((".pyc", ".pyo"))]
    license_files = [name for name in archive.namelist() if name.endswith("/licenses/LICENSE")]
    if len(license_files) != 1:
        raise SystemExit(f"wheel must contain one LICENSE, found {license_files}")
    packaged_license = archive.read(license_files[0])
if bad:
    raise SystemExit(f"wheel contains cache artifacts: {bad}")
if packaged_license != Path(sys.argv[2]).read_bytes():
    raise SystemExit("wheel LICENSE differs from the repository LICENSE")
PY

smoke_address_file="$worker_tmp_dir/worker-address"
smoke_log="$worker_tmp_dir/worker.log"
python "$worker_stage/tests/fake_worker_server.py" "$smoke_address_file" >"$smoke_log" 2>&1 &
worker_pid=$!
attempt=0
while [ ! -s "$smoke_address_file" ]; do
	if ! kill -0 "$worker_pid" >/dev/null 2>&1; then
		cat "$smoke_log" >&2
		echo "Python smoke Worker exited before becoming ready" >&2
		exit 1
	fi
	attempt=$((attempt + 1))
	if [ "$attempt" -ge 100 ]; then
		cat "$smoke_log" >&2
		echo "Python smoke Worker did not become ready" >&2
		exit 1
	fi
	sleep 0.05
done
smoke_address=$(cat "$smoke_address_file")
(
	cd "$core_dir"
	MEMORY_TEST_INFERENCE_GRPC_ADDR="$smoke_address" \
		go test -count=1 -run 'TestPythonWorker(Process|SourceMaterial)Contract' ./internal/inference/grpcworker
)
kill "$worker_pid" >/dev/null 2>&1 || true
wait "$worker_pid" >/dev/null 2>&1 || true
worker_pid=""

echo "[worker] protocol drift, tests, clean wheel and Go-to-Python smoke passed"
