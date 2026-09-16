#!/bin/sh

set -eu

container_name="memory-core-postgres-test-$$"
postgres_image="${MEMORY_TEST_POSTGRES_IMAGE:-postgres:17}"
memoryd_pid=""
log_file="$(mktemp -t memoryd-test.XXXXXX)"
test_addresses="$(
	python3 - <<'PY'
import socket

sockets = []
for _ in range(2):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    sockets.append(listener)
print(*(f"127.0.0.1:{listener.getsockname()[1]}" for listener in sockets))
PY
)"
http_address="${test_addresses%% *}"
grpc_address="${test_addresses#* }"

cleanup() {
	if [ -n "$memoryd_pid" ]; then
		kill "$memoryd_pid" >/dev/null 2>&1 || true
		wait "$memoryd_pid" >/dev/null 2>&1 || true
	fi
	docker rm -f "$container_name" >/dev/null 2>&1 || true
	rm -f "$log_file"
}
trap cleanup EXIT INT TERM

docker run --detach --rm \
	--name "$container_name" \
	-e POSTGRES_USER=memory \
	-e POSTGRES_PASSWORD=memory \
	-e POSTGRES_DB=memory \
	-p 127.0.0.1::5432 \
	"$postgres_image" >/dev/null

attempt=0
until docker exec "$container_name" pg_isready -U memory -d memory >/dev/null 2>&1; do
	attempt=$((attempt + 1))
	if [ "$attempt" -ge 120 ]; then
		echo "PostgreSQL did not become ready" >&2
		exit 1
	fi
	sleep 0.25
done

published_address="$(docker port "$container_name" 5432/tcp)"
postgres_port="${published_address##*:}"
database_url="postgres://memory:memory@127.0.0.1:${postgres_port}/memory?sslmode=disable"

MEMORY_TEST_DATABASE_URL="$database_url" \
	go test -count=1 ./internal/storage/postgres

go build -o bin/memoryd ./cmd/memoryd
MEMORYD_DATABASE_URL="$database_url" \
	MEMORYD_DATABASE_DRIVER=postgres \
	MEMORYD_HTTP_ADDR="$http_address" \
	MEMORYD_GRPC_ADDR="$grpc_address" \
	MEMORYD_AUTH_MODE=trusted_loopback \
	./bin/memoryd >"$log_file" 2>&1 &
memoryd_pid=$!

attempt=0
until curl --fail --silent "http://${http_address}/health/ready" >/dev/null 2>&1; do
	if ! kill -0 "$memoryd_pid" >/dev/null 2>&1; then
		if wait "$memoryd_pid"; then
			memoryd_status=0
		else
			memoryd_status=$?
		fi
		memoryd_pid=""
		cat "$log_file" >&2
		echo "memoryd exited before readiness with status $memoryd_status" >&2
		exit 1
	fi
	attempt=$((attempt + 1))
	if [ "$attempt" -ge 120 ]; then
		cat "$log_file" >&2
		echo "memoryd did not become ready" >&2
		exit 1
	fi
	sleep 0.25
done

MEMORY_TEST_GRPC_ADDR="$grpc_address" \
	go test -count=1 ./internal/transport/grpc

if ! kill "$memoryd_pid" >/dev/null 2>&1; then
	if wait "$memoryd_pid"; then
		memoryd_status=0
	else
		memoryd_status=$?
	fi
	memoryd_pid=""
	cat "$log_file" >&2
	echo "memoryd exited before shutdown with status $memoryd_status" >&2
	exit 1
fi
attempt=0
while kill -0 "$memoryd_pid" >/dev/null 2>&1; do
	attempt=$((attempt + 1))
	if [ "$attempt" -ge 40 ]; then
		cat "$log_file" >&2
		kill -KILL "$memoryd_pid" >/dev/null 2>&1 || true
		wait "$memoryd_pid" >/dev/null 2>&1 || true
		memoryd_pid=""
		echo "memoryd did not stop within 10 seconds" >&2
		exit 1
	fi
	sleep 0.25
done
if wait "$memoryd_pid"; then
	memoryd_status=0
else
	memoryd_status=$?
fi
memoryd_pid=""
if [ "$memoryd_status" -ne 0 ]; then
	cat "$log_file" >&2
	echo "memoryd graceful shutdown exited with status $memoryd_status" >&2
	exit 1
fi
