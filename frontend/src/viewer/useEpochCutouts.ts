import { useEffect, useRef, useState } from "react";
import type { Epoch } from "../types/api";
import { CutoutError, cutoutUrl, loadLiveCutout } from "./cutout";
import type { Cutout, LiveView } from "./cutout";

type Result = { cutout: Cutout } | { error: string };
type Results = Partial<Record<Epoch, Result>>;
type Queue = {
  controller: AbortController;
  results: Results;
  pending: Map<Epoch, AbortController>;
  required: Epoch[];
  prefetched: Set<Epoch>;
  next: () => void;
  attempt?: number;
};

export function useEpochCutouts(
  live: LiveView | null,
  first: Epoch,
  second: Epoch | null,
  attempt: number,
  enabled: boolean,
  prefetch?: Epoch,
) {
  // The URL for the coadd identifies the grid and band shared by all epochs.
  const field = live ? cutoutUrl({ ...live, epoch: "all" }) : null;
  const queue = useRef<Queue | null>(null);
  const [snapshot, setSnapshot] = useState<{ field: string; results: Results }>();

  useEffect(() => {
    if (!field) return;
    const controller = new AbortController();
    const work: Queue = {
      controller, results: {}, pending: new Map(), required: [], prefetched: new Set(), next: () => {},
    };
    queue.current = work;
    return () => {
      controller.abort();
      for (const request of work.pending.values()) request.abort();
    };
  }, [field]);

  useEffect(() => {
    const work = queue.current;
    if (!field || !work) return;
    const { signal } = work.controller;
    const foreground = second && first !== second ? [first, second] : [first];
    work.required = enabled ? foreground : [];
    for (const [epoch, request] of work.pending) {
      if (!enabled || (!foreground.includes(epoch) && epoch !== prefetch)) request.abort();
    }
    if (!enabled) return;

    const retry = work.attempt !== undefined && work.attempt !== attempt;
    work.attempt = attempt;
    const combined = work.results.all;
    const waitForLive = retry && combined && "cutout" in combined &&
      combined.cutout.source === "dr1-cube";
    if (retry) {
      for (const epoch of foreground) {
        const result = work.results[epoch];
        if (result && ("error" in result || result.cutout.source === "dr1-cube")) {
          delete work.results[epoch];
        }
      }
    }

    const fetchEpoch = async (epoch: Epoch) => {
      const params = new URLSearchParams(field.split("?")[1]);
      params.set("epoch", epoch);
      if (epoch === "all" && waitForLive) params.set("wait_for_live", "true");
      const request = new AbortController();
      work.pending.set(epoch, request);
      const signal = request.signal;
      try {
        const cutout = await loadLiveCutout("/api/cutout?" + params, signal);
        if (!signal.aborted) work.results[epoch] = { cutout };
      } catch (error) {
        const code = error instanceof CutoutError ? error.code : "SERVICE_UNAVAILABLE";
        if (!signal.aborted && (work.required.includes(epoch) || code === "NO_COVERAGE")) {
          work.results[epoch] = {
            error: code,
          };
        }
      } finally {
        work.pending.delete(epoch);
        if (!work.controller.signal.aborted) {
          if (!signal.aborted) setSnapshot({ field, results: { ...work.results } });
          work.next();
        }
      }
    };

    let timer: number | undefined;
    work.next = () => {
      if (signal.aborted) return;
      window.clearTimeout(timer);
      for (const epoch of foreground) {
        if (work.pending.size >= 2) break;
        if (!work.results[epoch] && !work.pending.has(epoch)) void fetchEpoch(epoch);
      }
      if (work.pending.size > 0 || !foreground.every((epoch) => work.results[epoch])) return;
      if (!foreground.some((epoch) => {
        const result = work.results[epoch];
        return result && "cutout" in result;
      })) return;
      if (prefetch && !work.results[prefetch] && !work.prefetched.has(prefetch)) {
        timer = window.setTimeout(() => {
          work.prefetched.add(prefetch);
          void fetchEpoch(prefetch);
        }, 900);
      }
    };
    if (retry) {
      queueMicrotask(() => {
        if (!signal.aborted) setSnapshot({ field, results: { ...work.results } });
      });
    }
    work.next();
    return () => {
      window.clearTimeout(timer);
      work.next = () => {};
    };
  }, [field, first, second, attempt, enabled, prefetch]);

  return snapshot?.field === field ? snapshot.results : {};
}
