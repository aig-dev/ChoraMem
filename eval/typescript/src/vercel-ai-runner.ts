import { generateText } from "ai";

export interface RunnerRequest {
  readonly model: string;
  readonly instructions: string;
  readonly input_text: string;
  readonly max_output_tokens: number;
}

export interface VercelGenerateRequest {
  readonly model: string;
  readonly system: string;
  readonly prompt: string;
  readonly maxOutputTokens: number;
}

export type GenerateText = (
  input: VercelGenerateRequest,
) => Promise<{ readonly text: string }>;

export type VercelAIRunner = (request: RunnerRequest) => Promise<string>;

export function parseRunnerRequest(line: string): RunnerRequest {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch (error) {
    throw new Error(`invalid runner request JSON: ${String(error)}`);
  }
  if (!isRecord(value)) {
    throw new Error("runner request must be a JSON object");
  }
  const expected = [
    "input_text",
    "instructions",
    "max_output_tokens",
    "model",
  ];
  const actual = Object.keys(value).sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`runner request fields must equal ${expected.join(", ")}`);
  }
  const { model, instructions, input_text: inputText } = value;
  if (typeof model !== "string" || model.trim() === "") {
    throw new Error("model must be nonblank text");
  }
  if (typeof instructions !== "string" || instructions.trim() === "") {
    throw new Error("instructions must be nonblank text");
  }
  if (typeof inputText !== "string" || inputText.trim() === "") {
    throw new Error("input_text must be nonblank text");
  }
  if (
    typeof value.max_output_tokens !== "number" ||
    !Number.isInteger(value.max_output_tokens) ||
    value.max_output_tokens <= 0
  ) {
    throw new Error("max_output_tokens must be a positive integer");
  }
  return {
    model,
    instructions,
    input_text: inputText,
    max_output_tokens: value.max_output_tokens,
  };
}

export function createVercelAIRunner(generate: GenerateText): VercelAIRunner {
  return async (request) => {
    const result = await generate({
      model: providerModel(request.model),
      system: request.instructions,
      prompt: request.input_text,
      maxOutputTokens: request.max_output_tokens,
    });
    if (typeof result.text !== "string" || result.text.trim() === "") {
      throw new Error("Vercel AI runner requires non-empty text output");
    }
    return result.text;
  };
}

export const runVercelAIRequest = createVercelAIRunner(async (input) =>
  generateText(input),
);

function providerModel(model: string): string {
  return model.startsWith("openai/") ? model : `openai/${model}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
