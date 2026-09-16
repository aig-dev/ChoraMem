import type { MessageInitShape, MessageShape } from "@bufbuild/protobuf";
import type { Transport } from "@connectrpc/connect";
import { generateText as officialGenerateText } from "ai";

import {
  createMemoryCoreClient,
  type MemoryCoreClient,
  type TokenProvider,
} from "./client.js";
import {
  ConstitutionSchema,
  EpisodeSourceRole,
  MemoryContextSchema,
  MemoryScopeSchema,
  OutcomeReceiptSchema,
  ReceiptAckSchema,
  SourceActorKind,
  SourceEventReceiptSchema,
} from "./gen/memory/v1/memory_pb.js";
import {
  renderMemoryContext,
  type RenderMemoryContextOptions,
} from "./render.js";

export type VercelGenerateTextInput = Parameters<typeof officialGenerateText>[0];

export interface TextResult {
  readonly text: string;
}

export type GenerateTextFunction<Result extends TextResult> = (
  input: VercelGenerateTextInput,
) => Promise<Result>;

export interface SourceProvenance {
  readonly sourceRef: string;
  readonly actorKind: SourceActorKind;
  readonly actorRef: string;
}

export interface ObservedSourceProvenance extends SourceProvenance {
  readonly idempotencyKey: string;
}

export interface TurnIdentity {
  readonly scope: MessageInitShape<typeof MemoryScopeSchema>;
  readonly runRef: string;
  readonly sourceGroupRef: string;
  readonly situation: ObservedSourceProvenance;
  readonly agentAct: ObservedSourceProvenance;
  readonly delivery: {
    readonly idempotencyKey: string;
  };
}

export interface MemoryGenerateTextInput {
  readonly turn: TurnIdentity;
  readonly situationText: string;
  readonly constitution?: MessageInitShape<typeof ConstitutionSchema>;
  readonly request: VercelGenerateTextInput;
}

export interface MemoryGenerateTextResult<Result extends TextResult> {
  readonly result: Result;
  readonly memoryContext: MessageShape<typeof MemoryContextSchema>;
  readonly situationReceipt: MessageShape<typeof SourceEventReceiptSchema>;
  readonly agentActReceipt: MessageShape<typeof SourceEventReceiptSchema>;
  readonly deliveryReceipt?: MessageShape<typeof ReceiptAckSchema>;
}

export interface ExplicitOutcome {
  readonly constitution?: MessageInitShape<typeof ConstitutionSchema>;
  readonly idempotencyKey: string;
  readonly scope: MessageInitShape<typeof MemoryScopeSchema>;
  readonly runRef: string;
  readonly sourceGroupRef: string;
  readonly text: string;
  readonly source: SourceProvenance;
  readonly deliveryReceiptRefs: readonly string[];
  readonly relatedSourceEventRefs: readonly string[];
}

export interface VercelAIMemoryAdapter<Result extends TextResult> {
  generateText(
    input: MemoryGenerateTextInput,
  ): Promise<MemoryGenerateTextResult<Result>>;
  reportOutcome(
    input: ExplicitOutcome,
  ): Promise<MessageShape<typeof OutcomeReceiptSchema>>;
}

export type VercelAIMemoryAdapterOptions<Result extends TextResult> = {
  readonly transport: Transport;
  readonly tokenProvider: TokenProvider;
  readonly generateText: GenerateTextFunction<Result>;
  readonly defaultTimeoutMs?: number;
  readonly episodeEvidenceMaxBytes?: number;
  readonly maxTokens?: number;
  readonly countTokens?: (text: string) => number;
};

export function createVercelAIMemoryAdapter<Result extends TextResult>(
  options: VercelAIMemoryAdapterOptions<Result>,
): VercelAIMemoryAdapter<Result> {
  const clientOptions =
    options.defaultTimeoutMs === undefined
      ? { tokenProvider: options.tokenProvider }
      : {
          tokenProvider: options.tokenProvider,
          defaultTimeoutMs: options.defaultTimeoutMs,
        };
  return new Adapter(
    createMemoryCoreClient(options.transport, clientOptions),
    options.generateText,
    adapterBudget(options),
  );
}

type AdapterBudget = {
  readonly episodeEvidenceMaxBytes: number;
  readonly renderOptions?: RenderMemoryContextOptions;
};

function adapterBudget<Result extends TextResult>(
  options: VercelAIMemoryAdapterOptions<Result>,
): AdapterBudget {
  const episodeEvidenceMaxBytes = options.episodeEvidenceMaxBytes ?? 0;
  if (
    !Number.isInteger(episodeEvidenceMaxBytes) ||
    episodeEvidenceMaxBytes < 0 ||
    episodeEvidenceMaxBytes > 16_384
  ) {
    throw new Error(
      "episodeEvidenceMaxBytes must be an integer from 0 to 16384",
    );
  }
  const hasRenderOptions =
    options.maxTokens !== undefined || options.countTokens !== undefined;
  let renderOptions: RenderMemoryContextOptions | undefined;
  if (options.maxTokens !== undefined && options.countTokens !== undefined) {
    renderOptions = {
      maxTokens: options.maxTokens,
      countTokens: options.countTokens,
    };
  } else if (options.maxTokens !== undefined) {
    renderMemoryContext({}, { maxTokens: options.maxTokens } as never);
  } else if (options.countTokens !== undefined) {
    renderMemoryContext({}, { countTokens: options.countTokens } as never);
  }
  if (hasRenderOptions && renderOptions !== undefined) {
    renderMemoryContext({}, renderOptions);
  }
  if (episodeEvidenceMaxBytes > 0 && renderOptions === undefined) {
    throw new Error("Episode evidence requires maxTokens and countTokens");
  }
  return renderOptions === undefined
    ? { episodeEvidenceMaxBytes }
    : { episodeEvidenceMaxBytes, renderOptions };
}

class Adapter<Result extends TextResult>
  implements VercelAIMemoryAdapter<Result>
{
  constructor(
    private readonly memory: MemoryCoreClient,
    private readonly generate: GenerateTextFunction<Result>,
    private readonly budget: AdapterBudget,
  ) {}

  async generateText(
    input: MemoryGenerateTextInput,
  ): Promise<MemoryGenerateTextResult<Result>> {
    const { turn } = input;
    const scope = { ...turn.scope };
    const constitution = input.constitution === undefined ? undefined : { ...input.constitution };
    const situationReceipt = await this.memory.observeSourceEvent({
      idempotencyKey: turn.situation.idempotencyKey,
      sourceEvent: {
        scope,
        text: input.situationText,
        sourceRef: turn.situation.sourceRef,
        actorKind: turn.situation.actorKind,
        actorRef: turn.situation.actorRef,
        ...(constitution === undefined ? {} : { constitution: { ...constitution } }),
      },
      episodeBinding: {
        runRef: turn.runRef,
        sourceGroupRef: turn.sourceGroupRef,
        role: EpisodeSourceRole.SITUATION,
      },
    });
    const memoryContext = await this.memory.selectMemory({
      scope,
      runRef: turn.runRef,
      situationSourceEventRefs: [situationReceipt.sourceEventRef],
      episodeEvidenceMaxBytes: this.budget.episodeEvidenceMaxBytes,
      ...(constitution === undefined
        ? {}
        : { constitution }),
    });

    const rendered = renderMemoryContext(
      memoryContext,
      this.budget.renderOptions,
    );
    let request = input.request;
    let deliveryReceipt: MessageShape<typeof ReceiptAckSchema> | undefined;
    if (rendered.text) {
      request = {
        ...request,
        system: injectSystem(request.system, rendered.text),
      };
      deliveryReceipt = await this.memory.recordMemoryDelivery({
        idempotencyKey: turn.delivery.idempotencyKey,
        scope,
        runRef: turn.runRef,
        memoryContextRef: memoryContext.contextRef,
        deliveredMemoryRefs: [...rendered.memoryRefs],
      });
    }

    const result = await this.generate(request);
    if (typeof result.text !== "string" || result.text.trim() === "") {
      throw new TypeError(
        "Vercel AI adapter requires a non-empty text result",
      );
    }
    const agentActReceipt = await this.memory.observeSourceEvent({
      idempotencyKey: turn.agentAct.idempotencyKey,
      sourceEvent: {
        scope,
        text: result.text,
        sourceRef: turn.agentAct.sourceRef,
        actorKind: turn.agentAct.actorKind,
        actorRef: turn.agentAct.actorRef,
      },
      episodeBinding: {
        runRef: turn.runRef,
        sourceGroupRef: turn.sourceGroupRef,
        role: EpisodeSourceRole.AGENT_ACT,
      },
    });

    const base = {
      result,
      memoryContext,
      situationReceipt,
      agentActReceipt,
    };
    return deliveryReceipt === undefined
      ? base
      : { ...base, deliveryReceipt };
  }

  reportOutcome(
    input: ExplicitOutcome,
  ): Promise<MessageShape<typeof OutcomeReceiptSchema>> {
    return this.memory.reportOutcome({
      idempotencyKey: input.idempotencyKey,
      scope: { ...input.scope },
      runRef: input.runRef,
      sourceGroupRef: input.sourceGroupRef,
      text: input.text,
      deliveryReceiptRefs: [...input.deliveryReceiptRefs],
      relatedSourceEventRefs: [...input.relatedSourceEventRefs],
      sourceRef: input.source.sourceRef,
      actorKind: input.source.actorKind,
      actorRef: input.source.actorRef,
      ...(input.constitution === undefined ? {} : { constitution: { ...input.constitution } }),
    });
  }
}

function injectSystem(
  existing: VercelGenerateTextInput["system"],
  memory: string,
): Exclude<VercelGenerateTextInput["system"], undefined> {
  if (existing === undefined) {
    return memory;
  }
  if (typeof existing === "string") {
    return `${existing}\n\n${memory}`;
  }
  const message = { role: "system" as const, content: memory };
  return Array.isArray(existing) ? [...existing, message] : [existing, message];
}
