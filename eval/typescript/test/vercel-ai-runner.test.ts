import { describe, expect, it } from "vitest";

import {
  createVercelAIRunner,
  parseRunnerRequest,
  type VercelGenerateRequest,
} from "../src/vercel-ai-runner.js";

const request = {
  model: "gpt-fixed",
  instructions: "Only answer from memory.",
  input_text: "What tea?",
  max_output_tokens: 64,
} as const;

describe("Vercel AI eval runner", () => {
  it("maps the shared text request to one generateText call", async () => {
    const calls: VercelGenerateRequest[] = [];
    const run = createVercelAIRunner(async (input) => {
      calls.push(input);
      return { text: "oolong" };
    });

    const output = await run(request);

    expect(output).toBe("oolong");
    expect(calls).toEqual([
      {
        model: "openai/gpt-fixed",
        system: "Only answer from memory.",
        prompt: "What tea?",
        maxOutputTokens: 64,
      },
    ]);
  });

  it("rejects empty model output", async () => {
    const run = createVercelAIRunner(async () => ({ text: "  " }));

    await expect(run(request)).rejects.toThrow("non-empty text");
  });

  it("fails closed on nested or malformed requests", () => {
    expect(() => parseRunnerRequest(JSON.stringify({ request }))).toThrow(
      "fields",
    );
    expect(() =>
      parseRunnerRequest(
        JSON.stringify({ ...request, max_output_tokens: false }),
      ),
    ).toThrow("positive integer");
  });
});
