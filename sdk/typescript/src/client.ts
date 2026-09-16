import {
  createClient,
  type CallOptions,
  type Client,
  type Transport,
} from "@connectrpc/connect";
import {
  createConnectTransport,
  Http2SessionManager,
} from "@connectrpc/connect-node";

import { MemoryCore } from "./gen/memory/v1/memory_pb.js";

type GeneratedClient = Client<typeof MemoryCore>;

export type TokenProvider = (tenantRef: string) => string | Promise<string>;

export type MemoryCoreClient = GeneratedClient & {
  close(): void;
};

export interface ClientOptions {
  readonly tokenProvider: TokenProvider;
  readonly defaultTimeoutMs?: number;
}

export interface ConnectOptions extends ClientOptions {
  readonly endpoint: string;
}

/** Wrap a caller-owned transport. Closing this client is a no-op. */
export function createMemoryCoreClient(
  transport: Transport,
  options: ClientOptions,
): MemoryCoreClient {
  return wrapClient(createClient(MemoryCore, transport), options);
}

/** Create and own one HTTP/2 transport, session manager, and abort signal. */
export function connectMemoryCore(options: ConnectOptions): MemoryCoreClient {
  const baseUrl = normalizeEndpoint(options.endpoint);
  const sessionManager = new Http2SessionManager(baseUrl);
  const controller = new AbortController();
  const transport = createConnectTransport({
    baseUrl,
    httpVersion: "2",
    sessionManager,
  });
  return wrapClient(createClient(MemoryCore, transport), options, {
    signal: controller.signal,
    close: () => {
      controller.abort();
      sessionManager.abort(new Error("Memory Core client closed"));
    },
  });
}

export function normalizeEndpoint(endpoint: string): string {
  const value = endpoint.trim();
  const url = new URL(value.includes("://") ? value : `http://${value}`);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    (url.pathname !== "/" && url.pathname !== "")
  ) {
    throw new Error("endpoint must be an http(s) origin");
  }
  return url.origin;
}

type OwnedLifecycle = {
  readonly signal: AbortSignal;
  readonly close: () => void;
};

function wrapClient(
  raw: GeneratedClient,
  options: ClientOptions,
  owned?: OwnedLifecycle,
): MemoryCoreClient {
  const defaultTimeoutMs = options.defaultTimeoutMs ?? 10_000;
  validateTimeout(defaultTimeoutMs, "defaultTimeoutMs");
  let closed = false;

  const call = async <Result>(
    tenantRef: string,
    invoke: (options: CallOptions) => Promise<Result>,
    callOptions: CallOptions = {},
  ): Promise<Result> => {
    if (closed) {
      throw new Error("Memory Core client is closed");
    }
    if (typeof tenantRef !== "string" || tenantRef.trim() === "") {
      throw new Error("request requires a nonblank tenantRef");
    }

    const timeoutMs = boundedTimeout(defaultTimeoutMs, callOptions.timeoutMs);
    const token = await options.tokenProvider(tenantRef);
    validateBearerToken(token);

    const headers = new Headers(callOptions.headers);
    headers.set("authorization", `Bearer ${token}`);
    const signal = callOptions.signal ?? owned?.signal;
    return invoke({
      ...callOptions,
      headers,
      timeoutMs,
      ...(signal === undefined ? {} : { signal }),
    });
  };

  return {
    observeSourceEvent: (request, callOptions) =>
      call(
        request.sourceEvent?.scope?.tenantRef ?? "",
        (boundedOptions) => raw.observeSourceEvent(request, boundedOptions),
        callOptions,
      ),
    selectMemory: (request, callOptions) =>
      call(
        request.scope?.tenantRef ?? "",
        (boundedOptions) => raw.selectMemory(request, boundedOptions),
        callOptions,
      ),
    recordMemoryDelivery: (request, callOptions) =>
      call(
        request.scope?.tenantRef ?? "",
        (boundedOptions) => raw.recordMemoryDelivery(request, boundedOptions),
        callOptions,
      ),
    reportOutcome: (request, callOptions) =>
      call(
        request.scope?.tenantRef ?? "",
        (boundedOptions) => raw.reportOutcome(request, boundedOptions),
        callOptions,
      ),
    close: () => {
      if (owned !== undefined && !closed) {
        closed = true;
        owned.close();
      }
    },
  };
}

function boundedTimeout(
  defaultTimeoutMs: number,
  requestedTimeoutMs: number | undefined,
): number {
  if (requestedTimeoutMs === undefined) {
    return defaultTimeoutMs;
  }
  validateTimeout(requestedTimeoutMs, "timeoutMs");
  return Math.min(defaultTimeoutMs, requestedTimeoutMs);
}

function validateTimeout(timeoutMs: number, name: string): void {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new Error(`${name} must be positive`);
  }
}

function validateBearerToken(token: unknown): asserts token is string {
  if (typeof token !== "string" || token.length === 0) {
    throw new Error("token provider returned a blank token");
  }
  if (/\p{White_Space}/u.test(token)) {
    throw new Error("Bearer token must not contain Unicode whitespace");
  }
}
