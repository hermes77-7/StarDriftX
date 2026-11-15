import { useEffect, useState } from "react";
import type { CatalogName, CatalogResponse, CatalogSource } from "../types/api";
import type { Coordinates } from "./search";
import { createRequests, fetchJson } from "./requests";

export const catalogs = ["gaia", "simbad", "wise"] as const;
export const catalogLabels = { gaia: "Gaia DR3", simbad: "SIMBAD", wise: "AllWISE" };
export const sourceKey = (source: CatalogSource) => [source.catalog, source.id].join(":");

const catalogRequests = createRequests<CatalogResponse>(24, () => 120000);

export function useCatalog(
  catalog: CatalogName, enabled: boolean, coordinates: Coordinates, fov: number,
  magnitude: number | undefined, attempt: number,
) {
  const params = new URLSearchParams({
    catalog, ra: String(coordinates.ra), dec: String(coordinates.dec),
    radius: String(Math.max(0.01, Number((fov / Math.SQRT2 + 0.002).toFixed(6)))),
  });
  if (magnitude !== undefined) params.set("mag_limit", String(magnitude));
  const url = enabled ? "/api/catalog?" + params : null;
  const [result, setResult] = useState<{ url: string; attempt: number; data?: CatalogResponse }>();
  useEffect(() => {
    if (!url) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void catalogRequests(url, controller.signal, (shared) =>
        fetchJson<CatalogResponse>(url, shared),
      ).then(
        (data) => {
          if (!controller.signal.aborted) setResult({ url, attempt, data });
        },
        () => {
          if (!controller.signal.aborted) setResult({ url, attempt });
        },
      );
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [url, attempt]);
  if (!url) return { state: "off" } as const;
  if (result?.url !== url || result.attempt !== attempt) return { state: "loading" } as const;
  return result.data ? { state: "ready", data: result.data } as const : { state: "error" } as const;
}

