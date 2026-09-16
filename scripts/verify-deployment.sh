#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
core_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
standalone_rendered=$(mktemp)

cleanup() {
	rm -f -- "$standalone_rendered"
}
trap cleanup EXIT HUP INT TERM

MEMORY_POSTGRES_PASSWORD=release-gate-password \
	MEMORY_WORKER_OPENAI_MODEL=release-gate-model \
	OPENAI_API_KEY=release-gate-key \
	MEMORYD_JWT_HS256_SECRET=release-gate-memory-auth-secret-32 \
	MEMORYD_JWT_ISSUER=release-gate-issuer \
	MEMORYD_JWT_AUDIENCE=memory-core \
	docker compose --project-directory "$core_dir" -f "$core_dir/compose.yaml" \
	config --format json >"$standalone_rendered"

if MEMORY_POSTGRES_PASSWORD= \
	MEMORY_WORKER_OPENAI_MODEL=release-gate-model \
	OPENAI_API_KEY=release-gate-key \
	MEMORYD_JWT_HS256_SECRET=release-gate-memory-auth-secret-32 \
	MEMORYD_JWT_ISSUER=release-gate-issuer \
	MEMORYD_JWT_AUDIENCE=memory-core \
	docker compose --project-directory "$core_dir" -f "$core_dir/compose.yaml" \
	config >/dev/null 2>&1; then
	echo "Compose accepted a missing MEMORY_POSTGRES_PASSWORD" >&2
	exit 1
fi

python3 - "$standalone_rendered" <<'PY'
import json
from pathlib import Path
import sys

configuration = json.loads(Path(sys.argv[1]).read_text())
services = configuration["services"]
networks = configuration["networks"]

expected_networks = {
    "postgres": {"data"},
    "worker": {"inference"},
    "memoryd": {"data", "inference"},
}
for service, expected in expected_networks.items():
    actual = set(services[service].get("networks", {}))
    if actual != expected:
        raise SystemExit(f"{service} networks = {sorted(actual)}; want {sorted(expected)}")

if networks["data"].get("internal") is not True:
    raise SystemExit("data network must be internal")
if networks["inference"].get("internal") is True:
    raise SystemExit("inference network must retain model-provider egress")

ports = services["memoryd"].get("ports", [])
actual_ports = {
    (port.get("host_ip"), int(port["published"]), port["target"])
    for port in ports
}
expected_ports = {
    ("127.0.0.1", 8080, 8080),
    ("127.0.0.1", 8081, 8081),
}
if actual_ports != expected_ports:
    raise SystemExit(f"memoryd published ports are not loopback-only: {ports}")

password = services["postgres"]["environment"].get("POSTGRES_PASSWORD")
database_url = services["memoryd"]["environment"].get("MEMORYD_DATABASE_URL", "")
if password != "release-gate-password" or "release-gate-password" not in database_url:
    raise SystemExit("database password is not sourced from MEMORY_POSTGRES_PASSWORD")

memoryd_environment = services["memoryd"]["environment"]
if memoryd_environment.get("MEMORYD_DATABASE_DRIVER") != "postgres":
    raise SystemExit("standalone memoryd must select the PostgreSQL Adapter")
if memoryd_environment.get("MEMORYD_AUTH_MODE") != "jwt":
    raise SystemExit("memoryd public data plane must default to JWT auth in Compose")
if memoryd_environment.get("MEMORYD_JWT_ISSUER") != "release-gate-issuer":
    raise SystemExit("memoryd JWT issuer was not forwarded")
if memoryd_environment.get("MEMORYD_JWT_AUDIENCE") != "memory-core":
    raise SystemExit("memoryd JWT audience was not forwarded")
PY

echo "[deployment] standalone PostgreSQL Compose contract passed"
