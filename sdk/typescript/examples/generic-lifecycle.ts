import {
  EpisodeSourceRole,
  type MemoryCoreClient,
  type MemoryScope,
  renderMemoryContext,
  type SourceActorKind,
} from "@chorai/memory-core";

export interface SourceIdentity {
  readonly sourceRef: string;
  readonly actorKind: SourceActorKind;
  readonly actorRef: string;
}

export interface TurnIdentity {
  readonly scope: MemoryScope;
  readonly runRef: string;
  readonly sourceGroupRef: string;
  readonly situationIdempotencyKey: string;
  readonly situation: SourceIdentity;
  readonly deliveryIdempotencyKey: string;
  readonly agentActIdempotencyKey: string;
  readonly agentAct: SourceIdentity;
}

export interface ExplicitOutcome {
  readonly idempotencyKey: string;
  readonly text: string;
  readonly source: SourceIdentity;
}

export type TextModel = (input: {
  readonly memoryText: string;
  readonly situationText: string;
}) => Promise<string>;

export async function runLifecycle(
  memory: MemoryCoreClient,
  input: {
    readonly identity: TurnIdentity;
    readonly situationText: string;
    readonly runModel: TextModel;
    readonly outcome: ExplicitOutcome;
    readonly episodeEvidence?: {
      readonly maxBytes: number;
      readonly maxTokens: number;
      readonly countTokens: (text: string) => number;
    };
  },
): Promise<string> {
  const { identity } = input;
  const situation = await memory.observeSourceEvent({
    idempotencyKey: identity.situationIdempotencyKey,
    sourceEvent: {
      scope: identity.scope,
      text: input.situationText,
      sourceRef: identity.situation.sourceRef,
      actorKind: identity.situation.actorKind,
      actorRef: identity.situation.actorRef,
    },
    episodeBinding: {
      runRef: identity.runRef,
      sourceGroupRef: identity.sourceGroupRef,
      role: EpisodeSourceRole.SITUATION,
    },
  });
  const context = await memory.selectMemory({
    scope: identity.scope,
    runRef: identity.runRef,
    situationSourceEventRefs: [situation.sourceEventRef],
    episodeEvidenceMaxBytes: input.episodeEvidence?.maxBytes ?? 0,
  });
  const rendered = renderMemoryContext(
    context,
    input.episodeEvidence === undefined
      ? undefined
      : {
          maxTokens: input.episodeEvidence.maxTokens,
          countTokens: input.episodeEvidence.countTokens,
        },
  );

  const delivery = rendered.text
    ? await memory.recordMemoryDelivery({
        idempotencyKey: identity.deliveryIdempotencyKey,
        scope: identity.scope,
        runRef: identity.runRef,
        memoryContextRef: context.contextRef,
        deliveredMemoryRefs: [...rendered.memoryRefs],
      })
    : undefined;

  // The model receives plain text only: never protobuf objects or stable refs.
  const output = await input.runModel({
    memoryText: rendered.text,
    situationText: input.situationText,
  });
  if (typeof output !== "string" || output.trim() === "") {
    throw new TypeError("model must return nonblank text");
  }

  const agentAct = await memory.observeSourceEvent({
    idempotencyKey: identity.agentActIdempotencyKey,
    sourceEvent: {
      scope: identity.scope,
      text: output,
      sourceRef: identity.agentAct.sourceRef,
      actorKind: identity.agentAct.actorKind,
      actorRef: identity.agentAct.actorRef,
    },
    episodeBinding: {
      runRef: identity.runRef,
      sourceGroupRef: identity.sourceGroupRef,
      role: EpisodeSourceRole.AGENT_ACT,
    },
  });

  // Outcome is explicit external evidence, never inferred from model output.
  await memory.reportOutcome({
    idempotencyKey: input.outcome.idempotencyKey,
    scope: identity.scope,
    runRef: identity.runRef,
    sourceGroupRef: identity.sourceGroupRef,
    text: input.outcome.text,
    deliveryReceiptRefs: delivery === undefined ? [] : [delivery.receiptRef],
    relatedSourceEventRefs: [agentAct.sourceEventRef],
    sourceRef: input.outcome.source.sourceRef,
    actorKind: input.outcome.source.actorKind,
    actorRef: input.outcome.source.actorRef,
  });
  return output;
}
