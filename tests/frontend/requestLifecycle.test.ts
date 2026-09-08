import { afterEach, describe, expect, it, vi } from 'vitest';
import { ReadApiError, type ReadApiResponse } from '../../services/readApi';
import { DefaultRequestOwner } from '../../services/requestLifecycle';

const result = (pollAfterSeconds: number | null): ReadApiResponse<{ value: string }> => ({
  body: { value: 'current' }, data: { value: 'current' }, etag: null, notModified: false, pollAfterSeconds,
});

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('DefaultRequestOwner', () => {
  it('schedules retained-envelope polling and stops when advice is null', async () => {
    vi.useFakeTimers();
    const owner = new DefaultRequestOwner();
    const load = vi.fn().mockResolvedValue(result(5));
    await owner.start({ load, getPollAfterSeconds: (value) => value.pollAfterSeconds, onData: vi.fn(), onError: vi.fn() });
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5000);
    expect(load).toHaveBeenCalledTimes(2);

    owner.cancel();
    const stopped = new DefaultRequestOwner();
    const stopLoad = vi.fn().mockResolvedValue(result(null));
    await stopped.start({ load: stopLoad, getPollAfterSeconds: (value) => value.pollAfterSeconds, onData: vi.fn(), onError: vi.fn() });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(stopLoad).toHaveBeenCalledTimes(1);
  });

  it('suppresses a late response after cancellation even if the loader ignores abort', async () => {
    const owner = new DefaultRequestOwner();
    let resolveRequest!: (value: ReadApiResponse<{ value: string }>) => void;
    const data = vi.fn();
    const pending = new Promise<ReadApiResponse<{ value: string }>>((resolve) => { resolveRequest = resolve; });
    const started = owner.start({ load: () => pending, getPollAfterSeconds: () => null, onData: data, onError: vi.fn() });
    owner.cancel();
    resolveRequest(result(null));
    await started;
    expect(data).not.toHaveBeenCalled();
  });

  it('honors Retry-After and bounds retryable failures', async () => {
    vi.useFakeTimers();
    const owner = new DefaultRequestOwner();
    const load = vi.fn()
      .mockRejectedValueOnce(new ReadApiError('busy', { status: 503, retryable: true, retryAfterMs: 2000 }))
      .mockResolvedValueOnce(result(null));
    const started = owner.start({ load, getPollAfterSeconds: (value) => value.pollAfterSeconds, onData: vi.fn(), onError: vi.fn() });
    await vi.advanceTimersByTimeAsync(1999);
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await started;
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('settles a retry backoff promise when cancelled', async () => {
    vi.useFakeTimers();
    const owner = new DefaultRequestOwner();
    const load = vi.fn().mockRejectedValue(new ReadApiError('offline', { retryable: true }));
    const started = owner.start({ load, getPollAfterSeconds: () => null, onData: vi.fn(), onError: vi.fn() });
    await Promise.resolve();
    owner.cancel();
    await expect(started).resolves.toBeUndefined();
    expect(load).toHaveBeenCalledTimes(1);
  });
});
