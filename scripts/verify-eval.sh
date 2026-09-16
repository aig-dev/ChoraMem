#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
core_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
eval_tmp_dir=$(mktemp -d)

cleanup() {
	rm -rf -- "$eval_tmp_dir"
}
trap cleanup EXIT HUP INT TERM

python3 -m venv "$eval_tmp_dir/venv"
PATH="$eval_tmp_dir/venv/bin:$PATH"
export PATH PYTHONDONTWRITEBYTECODE=1
python -m pip install --disable-pip-version-check --quiet \
	"$core_dir/sdk/python[test]" \
	"$core_dir/worker/python" \
	"$core_dir/eval/python[test,codegen,cupid,anchor,local-index]"

"$core_dir/eval/python/scripts/generate-memory-index-bindings.sh" \
	"$eval_tmp_dir/generated"
diff -u \
	"$core_dir/eval/python/src/memoryindex/v1/memory_index_pb2.py" \
	"$eval_tmp_dir/generated/memoryindex/v1/memory_index_pb2.py"
diff -u \
	"$core_dir/eval/python/src/memoryindex/v1/memory_index_pb2_grpc.py" \
	"$eval_tmp_dir/generated/memoryindex/v1/memory_index_pb2_grpc.py"

(
	cd "$core_dir/eval/python"
	python -m pytest -q -p no:cacheprovider
	python -m compileall -q src
)

python -m pip wheel --disable-pip-version-check --quiet --no-deps \
	--wheel-dir "$eval_tmp_dir/wheel" "$core_dir/eval/python"

(
	cd "$core_dir/eval/typescript"
	npm ci --ignore-scripts
	npm run typecheck
	npm test
	npm run build
)

invalid_response=$(printf '%s\n' '{}' | node "$core_dir/eval/typescript/bin/vercel-ai-runner.mjs" || true)
python - "$invalid_response" <<'PY'
import json
import sys

response = json.loads(sys.argv[1])
if set(response) != {"error"} or "fields" not in response["error"]:
    raise SystemExit(f"unexpected Vercel runner failure response: {response!r}")
PY

echo "[eval] harness adapters, PersonaMem/PERMA/CUPID/ANCHOR/delayed Seed/generalization gates and failure semantics passed"
