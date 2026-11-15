// Each subscriber can leave without aborting work still needed by another viewer.
export function createRequests<T>(capacity: number, ttl: (value: T) => number) {
  const cache = new Map<string, { value: T; expires: number }>();
  const pending = new Map<string, {
    controller: AbortController;
    promise: Promise<T>;
    subscribers: number;
  }>();

  return async function request(
    key: string, signal: AbortSignal, load: (signal: AbortSignal) => Promise<T>,
  ): Promise<T> {
    signal.throwIfAborted();
    const saved = cache.get(key);
    if (saved) {
      cache.delete(key);
      if (saved.expires > Date.now()) {
        cache.set(key, saved);
        return saved.value;
      }
    }
    let work = pending.get(key);
    if (!work) {
      const controller = new AbortController();
      const promise = Promise.resolve()
        .then(() => {
          controller.signal.throwIfAborted();
          return load(controller.signal);
        }).then((value) => {
          controller.signal.throwIfAborted();
          const duration = ttl(value);
          if (duration > 0) {
            cache.set(key, { value, expires: Date.now() + duration });
            if (cache.size > capacity) cache.delete(cache.keys().next().value!);
          }
          return value;
        }).finally(() => {
          if (pending.get(key)?.controller === controller) pending.delete(key);
        });
      work = { controller, promise, subscribers: 0 };
      pending.set(key, work);
    }
    const job = work;
    job.subscribers++;
    return new Promise<T>((resolve, reject) => {
      const release = () => {
        signal.removeEventListener("abort", abort);
        job.subscribers--;
        // React can subscribe again in the same render before the request leaves.
        queueMicrotask(() => {
          if (job.subscribers === 0 && pending.get(key) === job) {
            pending.delete(key);
            job.controller.abort();
          }
        });
      };
      const abort = () => {
        release();
        reject(signal.reason);
      };
      signal.addEventListener("abort", abort, { once: true });
      job.promise.then(
        (value) => { if (!signal.aborted) { release(); resolve(value); } },
        (error) => { if (!signal.aborted) { release(); reject(error); } },
      );
    });
  };
}

export async function fetchJson<T>(url: string, signal: AbortSignal, timeout = 25000): Promise<T> {
  const response = await fetch(url, { signal: AbortSignal.any([signal, AbortSignal.timeout(timeout)]) });
  if (!response.ok) throw new HttpError(response.status);
  return response.json();
}

export class HttpError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Request failed (${status})`);
    this.status = status;
  }
}
