#!/usr/bin/env node

import {
  parseRunnerRequest,
  runVercelAIRequest,
} from "../dist/vercel-ai-runner.js";

try {
  let source = "";
  for await (const chunk of process.stdin) {
    source += chunk;
  }
  const lines = source.split(/\r?\n/u).filter((line) => line.trim() !== "");
  if (lines.length !== 1) {
    throw new Error(`runner requires exactly one request line; got ${lines.length}`);
  }
  const output = await runVercelAIRequest(parseRunnerRequest(lines[0]));
  process.stdout.write(`${JSON.stringify({ output_text: output })}\n`);
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  process.stdout.write(`${JSON.stringify({ error: message })}\n`);
  process.exitCode = 1;
}
