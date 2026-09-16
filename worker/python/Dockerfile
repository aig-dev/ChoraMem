FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir '.[openai]'

ENV MEMORY_WORKER_GRPC_ADDR=0.0.0.0:8082
EXPOSE 8082

USER 65532:65532

ENTRYPOINT ["memory-core-worker"]
