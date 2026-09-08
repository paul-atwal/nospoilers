import { ReadApiError, RETRY_MAX_DELAY_MS, type ReadApiResponse } from './readApi';

export interface RequestOwnerOptions<T> {
  load: (signal: AbortSignal) => Promise<ReadApiResponse<T>>;
  getPollAfterSeconds: (response: ReadApiResponse<T>) => number | null;
  onData: (response: ReadApiResponse<T>) => void;
  onError: (error: unknown) => void;
  onLoading?: (loading: boolean) => void;
  onRetry?: (error: unknown, delayMs: number) => void;
  retryLimit?: number;
}

export interface RequestOwner {
  start<T>(options: RequestOwnerOptions<T>): Promise<void>;
  refresh(): Promise<void>;
  retry(): Promise<void>;
  cancel(): void;
  isActive(): boolean;
}

const isAbortError = (error: unknown): boolean => (
  (error instanceof DOMException && error.name === 'AbortError')
  || (error instanceof Error && error.name === 'AbortError')
);

const isRetryable = (error: unknown): boolean => (
  error instanceof ReadApiError
    ? error.retryable
    : !isAbortError(error)
);

const retryDelay = (error: unknown, attempt: number): number => {
  if (error instanceof ReadApiError && error.retryAfterMs !== null) {
    return Math.min(RETRY_MAX_DELAY_MS, Math.max(250, error.retryAfterMs));
  }
  // Deliberately bounded: a bad connection should not create a tight loop.
  return Math.min(RETRY_MAX_DELAY_MS, Math.max(500, 1000 * (2 ** Math.min(attempt, 4))));
};

/**
 * Owns one request and one timer. A generation is invalidated on every start
 * and cancel, so even fetch implementations that ignore AbortSignal cannot
 * commit a response belonging to an older selection.
 */
export class DefaultRequestOwner implements RequestOwner {
  private generation = 0;
  private controller: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inFlight: Promise<void> | null = null;
  private options: RequestOwnerOptions<unknown> | null = null;
  private retryAttempt = 0;

  async start<T>(options: RequestOwnerOptions<T>): Promise<void> {
    this.cancel();
    this.options = options as RequestOwnerOptions<unknown>;
    this.retryAttempt = 0;
    await this.run(this.generation);
  }

  refresh(): Promise<void> {
    if (!this.options) return Promise.resolve();
    if (this.inFlight) return this.inFlight;
    this.clearTimer();
    this.retryAttempt = 0;
    return this.run(this.generation);
  }

  retry(): Promise<void> {
    if (!this.options) return Promise.resolve();
    const options = this.options;
    this.cancel();
    this.options = options;
    this.retryAttempt = 0;
    return this.run(this.generation);
  }

  cancel(): void {
    this.generation += 1;
    this.clearTimer();
    this.controller?.abort();
    this.controller = null;
    this.inFlight = null;
    this.options?.onLoading?.(false);
  }

  isActive(): boolean { return this.inFlight !== null || this.timer !== null; }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private run(generation: number): Promise<void> {
    if (!this.options || this.inFlight) return this.inFlight ?? Promise.resolve();
    const options = this.options;
    const controller = new AbortController();
    this.controller = controller;
    options.onLoading?.(true);
    const operation = this.attempt(generation, controller, options, 0)
      .finally(() => {
        if (this.generation === generation) {
          this.inFlight = null;
          this.controller = null;
          options.onLoading?.(false);
        }
      });
    this.inFlight = operation;
    return operation;
  }

  private async attempt<T>(
    generation: number,
    controller: AbortController,
    options: RequestOwnerOptions<T>,
    attempt: number,
  ): Promise<void> {
    try {
      const response = await options.load(controller.signal);
      if (generation !== this.generation || controller.signal.aborted) return;
      options.onData(response);
      this.retryAttempt = 0;
      this.schedule(generation, options.getPollAfterSeconds(response));
    } catch (error) {
      if (generation !== this.generation || controller.signal.aborted || isAbortError(error)) return;
      options.onError(error);
      const limit = options.retryLimit ?? 3;
      if (isRetryable(error) && attempt < limit) {
        const delay = retryDelay(error, attempt);
        options.onRetry?.(error, delay);
        await new Promise<void>((resolve) => {
          this.timer = setTimeout(() => {
            this.timer = null;
            resolve();
          }, delay);
        });
        if (generation !== this.generation || controller.signal.aborted) return;
        await this.attempt(generation, controller, options, attempt + 1);
      }
    }
  }

  private schedule<T>(generation: number, seconds: number | null): void {
    this.clearTimer();
    if (seconds === null || seconds < 0 || generation !== this.generation) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      if (generation === this.generation) void this.run(generation);
    }, seconds * 1000);
  }
}

export const createRequestOwner = (): RequestOwner => new DefaultRequestOwner();
