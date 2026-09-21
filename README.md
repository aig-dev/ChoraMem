# ChoraMem

[English](README.md) | [简体中文](README.zh-CN.md)

Research Alpha · `v0.1.0-alpha.1`

ChoraMem is an open-source long-term memory framework inspired by the *Cheng Weishi Lun* (成唯识论). It is designed for long-running conversations, personalized agents, and AI companionship.

It manages episodes, recollections, and latent response tendencies as an independent service. It returns a neutral `MemoryContext` over HTTP/gRPC, while the Agent Harness owns prompt composition, planning, and action.

## Design Principles

ChoraMem draws structural inspiration from the causal relationship among seeds, manifestation, and perfuming in the *Cheng Weishi Lun*. A Seed represents a latent tendency that may shape future understanding, attention, and response.

Relevant situations allow a tendency to participate in the current response. New experience and feedback from that interaction continue to shape future tendencies.

The engineering model uses a limited conceptual correspondence. Source links and append-only versions record how memories evolve. See [Philosophy and concepts](docs/philosophy.zh-CN.md) for definitions and references.

| Concept | Role |
|---|---|
| `Episode` | Stores a concrete situation, an Agent action, and an optional later outcome |
| `Recollection` | Stores a revisable long-term memory of a fact, preference, agreement, or experience |
| `Disposition` (Seed) | Represents a latent tendency that may shape future understanding and response in similar situations |
| `Constitution` | Provides a versioned role baseline maintained by the Harness |
| `MemoryContext` | Provides neutral context selected and frozen for the current situation |

## Memory Loop

```text
Interaction experience (Episode)
  -> Background consolidation (ConsolidateWindow)
  -> Recollections and dispositions (Recollection / Disposition)
  -> Online selection (SelectMemory)
  -> Neutral context (MemoryContext)
  -> Harness injection and actual action
  -> Later outcome (Outcome)
  -> Next consolidation and memory selection
```

A Recollection can be reconsolidated directly by new experience. A Seed forms from repeated, source-independent experiences across sessions.

An existing Seed can receive an explicit long-term correction, or be revised and locally suppressed through a complete chain of delivery, action, and non-Agent outcome.

Memory selection uses both memory text and linked experiences. Relevant Episodes provide situational grounding for Seeds. Agent and relationship scopes isolate long-term memory, while source links and version history preserve its evolution.

Generative text processing runs in the background Python Worker and returns plain text or shallow tagged text. Online `SelectMemory` uses deterministic rules and an optional semantic index to select memories and freeze a `MemoryContext`.

The Core validates sources, write eligibility, versions, and transactional commits.

## Architecture and Integration

ChoraMem originated in Chorai and independently owns its public protocol and memory state. The Go `memoryd` service exposes native gRPC and HTTP/Connect JSON APIs.

Harnesses use thin Adapters to submit source events, inject selected context, and report feedback.

Relational storage supports PostgreSQL and MySQL 8. Each deployment selects one `CoreStore` Adapter. Semantic retrieval uses a replaceable `MemoryIndex` Provider whose index projection can be rebuilt from the relational store.

| SDK | Integration | Documentation |
|---|---|---|
| Go | Public protocol client | [Go SDK](sdk/go/README.md) |
| Python | OpenAI Agents SDK Adapter | [Python SDK](sdk/python/README.md) |
| TypeScript | Vercel AI SDK Adapter | [TypeScript SDK](sdk/typescript/README.md) |

The public protocol provides four lifecycle methods: `ObserveSourceEvent`, `SelectMemory`, `RecordMemoryDelivery`, and `ReportOutcome`.

The Harness maintains the role baseline, chooses where to place memory in the prompt, and runs models, tools, and actions.

## Quick Start

The reference deployment uses Docker Compose, PostgreSQL, and the Python Worker. By default, the Worker calls a text model endpoint compatible with the OpenAI Responses API.

```bash
cp .env.example .env
```

Set the database password, JWT configuration, model name, and API key in `.env`, then start the services.

```bash
docker compose up --build -d
```

Check the readiness endpoint after the services start.

```bash
curl -f http://127.0.0.1:8080/health/ready
```

- HTTP/Connect JSON: `http://127.0.0.1:8080/memory.v1.MemoryCore/<Method>`
- Native gRPC: `127.0.0.1:8081`

Public calls use tenant-bound JWTs. Connection and authentication parameters are documented in each SDK. This release is a Research Alpha; APIs and data structures may evolve.

## Documentation and Validation

- [Design philosophy and references](docs/philosophy.zh-CN.md) (Chinese)
- [Architecture and lifecycle](docs/architecture.zh-CN.md) (Chinese)
- [Semantic index protocol](docs/memory-index.zh-CN.md) (Chinese)
- [Python Worker](worker/python/README.md)
- [Evaluation protocol](eval/README.zh-CN.md) and [experiment reports](eval/reports/) (Chinese)

Offline validation covers the Core, both database contracts, SDKs, Worker, deployment configuration, and evaluation workflow. Real-model evaluation is enabled explicitly through the evaluation entry point.

```bash
make verify
make release-gate
```

## License

Copyright 2026 ChoraMem contributors.

ChoraMem is licensed under the [Apache License 2.0](LICENSE).
