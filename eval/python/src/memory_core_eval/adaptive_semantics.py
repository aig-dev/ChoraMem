"""Opt-in finite live Worker diagnostic; raw text still needs semantic review."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path

from .personamem_live import ChatTextModel


async def run(args):
    import grpc
    from memory_core_worker.service import InferenceService, build_model_input
    from memory_core_worker.v1 import inference_pb2 as pb, inference_pb2_grpc as rpc

    args.output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location("adaptive_fixture_input", args.fixtures)
    import sys
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cases = module.CASES
    manifest = {"fixture_sha256": hashlib.sha256(args.fixtures.read_bytes()).hexdigest(),
                "prompt_sha256": hashlib.sha256(build_model_input(window_text="", allowed_target_refs=(), allowed_basis_refs=()).encode()).hexdigest(),
                "cases": [case.name for case in cases], "model": os.environ["MEMORY_WORKER_OPENAI_MODEL"],
                "thinking_enabled": args.thinking,
                "max_retries": 0, "max_output_tokens": args.max_output_tokens,
                "request_timeout": args.request_timeout, "rpc_timeout": args.rpc_timeout,
                "semantic_verdict": "requires raw-text review; shape pass is not semantic pass"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    failures = 0
    for case in cases:
        model = ChatTextModel(model=manifest["model"], api_key=os.environ["OPENAI_API_KEY"],
                              base_url=os.environ["OPENAI_BASE_URL"], usage_file=args.output / f"{case.name}.usage.jsonl", thinking_enabled=args.thinking,
                              max_retries=0, request_timeout=args.request_timeout)
        record = {"case": case.name, "rubric": case.rubric, "expected_shapes": case.expected,
                  "alternative_shapes": case.alternatives}
        create = model.client.chat.completions.create

        async def capture(**request):
            response = await create(**request)
            record["raw_output"] = response.choices[0].message.content
            return response

        model.client.chat.completions.create = capture

        class Model:
            async def complete(self, text):
                record["input"] = text
                return await model.complete(instructions="", input_text=text, max_output_tokens=args.max_output_tokens)

        server = grpc.aio.server()
        rpc.add_InferenceWorkerServicer_to_server(InferenceService(Model()), server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        try:
            response = await rpc.InferenceWorkerStub(channel).ProcessConsolidationWindow(pb.ProcessConsolidationWindowRequest(
                window_text=case.window, allowed_target_refs=case.targets, allowed_basis_refs=case.basis), timeout=args.rpc_timeout)
            record["output"] = response.tagged_text
            lines = response.tagged_text.splitlines()
            actual = [(lines[i+1], lines[i+5]) for i, line in enumerate(lines) if line == "TARGET"]
            record["shape_pass"] = any(sorted(actual) == sorted(expected) for expected in (case.expected, *case.alternatives))
            failures += not record["shape_pass"]
        except Exception as error:
            record["error"] = str(error)
            failures += 1
        finally:
            await channel.close()
            await server.stop(None)
            await model.close()
            with (args.output / "windows.jsonl").open("a") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps({k: record[k] for k in ("case", "shape_pass", "error") if k in record}), flush=True)
    print(f"shape failures: {failures}/{len(cases)}; inspect raw text before semantic verdict", flush=True)
    return bool(failures)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--request-timeout", type=float, default=110)
    parser.add_argument("--rpc-timeout", type=float, default=180)
    args = parser.parse_args(argv)
    if args.max_output_tokens <= 0:
        parser.error("--max-output-tokens must be positive")
    for name in ("request_timeout", "rpc_timeout"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    return args


def main():
    raise SystemExit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()
