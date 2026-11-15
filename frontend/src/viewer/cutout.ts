import fixtureUrl from "./fixture/scope.f32?url&no-inline";
import fixture from "./fixture/scope.json";
import m31Url from "./fixture/m31.f32?url&no-inline";
import m31Fixture from "./fixture/m31.json";
import { decodePayload } from "../utils/payload";
import type {
  BandInfo, CutoutBinaryPayload, CutoutHeader, Epoch, ErrorCode, FovLimits, MetaResponse,
} from "../types/api";
import { createRequests, fetchJson } from "./requests";

export type CutoutMetadata = Omit<typeof fixture, "release" | "channel"> & {
  release: string | null;
  exposureCount?: number;
  exposureRange?: [number, number];
  epoch?: Epoch;
  band?: number;
  channel?: number;
  wavelengthRangeMicrons?: [number, number];
  flaggedFraction?: number;
  coverageFraction?: number;
  masked?: boolean;
  photometricNormalized?: boolean;
  warnings?: string[];
  collections?: string[];
  source?: CutoutHeader["source"];
};
export type Cutout = CutoutMetadata & { pixels: Float32Array };

export const bundledCutouts = {
  scope: { metadata: fixture, url: fixtureUrl },
  m31: { metadata: m31Fixture, url: m31Url },
};
export type Sample = keyof typeof bundledCutouts;

export function getSampleView(sample: Sample) {
  const { metadata } = bundledCutouts[sample];
  return {
    ra: metadata.wcs.referenceDegrees[0],
    dec: metadata.wcs.referenceDegrees[1],
    fov: Number(
      (metadata.width * Math.abs(metadata.wcs.degreesPerPixel[0])).toFixed(6),
    ),
    band: metadata.channel,
  };
}

export const sampleView = getSampleView("scope");

export const defaultFovLimits: FovLimits = {
  min_deg: 0.05,
  max_deg: 1,
  default_deg: 0.2,
};
export const defaultPixelScale = 6.15;

export type SpectralSelection =
  | { mode?: "single"; band: number }
  | { mode: "stack"; detector: number };

export type LiveView = SpectralSelection & {
  ra: number;
  dec: number;
  fov: number;
  epoch: Epoch;
};

export const detectorCount = 6;
export const channelsPerDetector = 17;
export const bandCount = detectorCount * channelsPerDetector;
export const detectorForBand = (band: number) => Math.ceil(band / channelsPerDetector);

export class CutoutError extends Error {
  readonly code: ErrorCode;

  constructor(code: ErrorCode, message: string) {
    super(message);
    this.code = code;
  }
}

export function cutoutUrl(view: LiveView) {
  const { ra, dec, fov, epoch } = view;
  const params = new URLSearchParams({
    ra: String(ra),
    dec: String(dec),
    fov: String(fov),
    epoch,
  });
  if (view.mode === "stack") {
    params.set("mode", "stack");
    params.set("detector", String(view.detector));
  } else params.set("band", String(view.band));
  return `/api/cutout?${params}`;
}

const recent = new Map<string, { etag: string; cutout: Cutout }>();
const liveRequests = createRequests<{ cutout: Cutout; reusable: boolean }>(
  6, ({ reusable }) => reusable ? 20000 : 0,
);
const metadataRequests = createRequests<MetaResponse>(8, () => 60000);
const sampleRequests = createRequests<Cutout>(2, () => Infinity);

export type M31Bundle = {
  release: "spherex_dr1";
  bands: (BandInfo & { file: string })[];
};

const m31Path = `${import.meta.env.BASE_URL}data/m31-dr1/`;
const m31Manifest = createRequests<M31Bundle | null>(1, (bundle) => bundle ? Infinity : 0);
const m31Bands = createRequests<Cutout>(12, () => Infinity);

export function loadM31Bundle(signal: AbortSignal): Promise<M31Bundle | null> {
  const url = m31Path + "manifest.json";
  return m31Manifest(url, signal, async (shared) => {
    const response = await fetch(url, {
      signal: AbortSignal.any([shared, AbortSignal.timeout(8000)]),
    });
    if (!response.ok || response.headers.get("Content-Type")?.includes("text/html")) return null;
    const bundle = await response.json() as M31Bundle;
    if (bundle.release !== "spherex_dr1" || !Array.isArray(bundle.bands) ||
        !bundle.bands.some(({ band }) => band === 4) ||
        bundle.bands.some(({ band, file }) => !Number.isInteger(band) || band < 1 ||
          band > bandCount || !/^band-\d{3}-[0-9a-f]{16}\.spx1$/.test(file))) {
      throw new Error("Invalid M31 DR1 band index.");
    }
    return bundle;
  });
}

export function loadM31Band(bundle: M31Bundle, band: number, signal: AbortSignal): Promise<Cutout> {
  const file = bundle.bands.find((item) => item.band === band)?.file;
  if (!file) return Promise.reject(new CutoutError("NO_COVERAGE", "This bundled band is blank."));
  const url = m31Path + file;
  return m31Bands(url, signal, async (shared) => {
    const response = await fetch(url, {
      signal: AbortSignal.any([shared, AbortSignal.timeout(15000)]),
    });
    if (!response.ok) throw new Error(`M31 band request failed (${response.status})`);
    const payload = await decodePayload(await response.arrayBuffer());
    const { header } = payload;
    const { wcs } = header;
    if (header.source !== "m31-dr1" || header.data_release !== bundle.release ||
        header.band !== band || header.detector !== detectorForBand(band) ||
        wcs.radesys !== "ICRS" || wcs.ctype.join(",") !== "RA---TAN,DEC--TAN" ||
        wcs.shape[0] !== m31Fixture.height || wcs.shape[1] !== m31Fixture.width ||
        wcs.crval.some((value, axis) => value !== m31Fixture.wcs.referenceDegrees[axis]) ||
        wcs.crpix.some((value, axis) => value !== m31Fixture.wcs.referencePixel[axis]) ||
        wcs.cdelt.some((value, axis) => value !== m31Fixture.wcs.degreesPerPixel[axis])) {
      throw new Error("Unexpected M31 DR1 sky grid or band.");
    }
    return cutoutFromPayload(payload);
  });
}

export async function loadLiveCutout(
  url: string,
  signal: AbortSignal,
): Promise<Cutout> {
  const { cutout } = await liveRequests(url, signal, (shared) => fetchCutout(url, shared));
  return cutout;
}

async function fetchCutout(url: string, signal: AbortSignal) {
  const cached = recent.get(url);
  const response = await fetch(url, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(175000)]),
    cache: "no-store",
    headers: cached ? { "If-None-Match": cached.etag } : {},
  });
  if (response.status === 304 && cached) {
    signal.throwIfAborted();
    recent.delete(url);
    recent.set(url, cached);
    return { cutout: cached.cutout, reusable: true };
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new CutoutError(
      body?.error?.code ?? "SERVICE_UNAVAILABLE",
      body?.error?.message ?? `Cutout request failed (${response.status})`,
    );
  }
  const payload = await decodePayload(await response.arrayBuffer());
  const { header } = payload;
  signal.throwIfAborted();
  const { wcs } = header;
  const requested = new URL(url, window.location.origin).searchParams;
  const stack = requested.get("mode") === "stack";
  const band = Number(requested.get("band"));
  const wrongSelection = stack
    ? header.band !== undefined || header.detector !== Number(requested.get("detector"))
    : header.band !== band || header.detector !== detectorForBand(band);
  if (
    (header.source !== "irsa" &&
      !(header.source === "dr1-cube" && header.data_release === "spherex_dr1" &&
        requested.get("epoch") === "all")) ||
    wcs.radesys !== "ICRS" ||
    wcs.ctype[0] !== "RA---TAN" ||
    wcs.ctype[1] !== "DEC--TAN" ||
    wrongSelection ||
    header.epoch !== requested.get("epoch") ||
    wcs.crval[0] !== Number(requested.get("ra")) ||
    wcs.crval[1] !== Number(requested.get("dec"))
  ) {
    throw new CutoutError(
      "SERVICE_UNAVAILABLE",
      "The live service returned an unexpected sky grid.",
    );
  }
  const cutout = cutoutFromPayload(payload);
  const etag = response.headers.get("ETag");
  recent.delete(url);
  const reusable = Boolean(etag) && !response.headers.get("Cache-Control")?.includes("no-store");
  if (etag && reusable) {
    recent.set(url, { etag, cutout });
    if (recent.size > 6) recent.delete(recent.keys().next().value!);
  }
  return { cutout, reusable };
}

function cutoutFromPayload({ header, data }: CutoutBinaryPayload): Cutout {
  const { wcs } = header;
  return {
    width: wcs.shape[1],
    height: wcs.shape[0],
    pixels: data,
    wcs: {
      projection: "TAN",
      frame: wcs.radesys,
      referenceDegrees: wcs.crval,
      referencePixel: wcs.crpix,
      degreesPerPixel: wcs.cdelt,
    },
    unit: header.bunit,
    detector: header.detector ?? 1,
    channel: header.channel,
    wavelengthMicrons: header.wavelength_um,
    wavelengthRangeMicrons: header.wavelength_range_um,
    exposureMjd: header.exposure_mjd ?? [],
    exposureCount: header.nexp,
    exposureRange: header.mjd_range,
    release: header.data_release,
    band: header.band,
    epoch: header.epoch,
    flaggedFraction: header.flagged_fraction,
    coverageFraction: header.coverage_fraction,
    masked: header.mask_flags,
    photometricNormalized: header.photometric_normalized,
    warnings: header.warnings,
    collections: header.collections,
    source: header.source,
  };
}

export async function loadBandMetadata(
  epoch: Epoch, release: string, signal: AbortSignal,
): Promise<BandInfo[]> {
  const params = new URLSearchParams({ include_bands: "true", epoch });
  const url = "/api/meta?" + params;
  const meta = await metadataRequests(url + "#" + release, signal, (shared) =>
    fetchJson<MetaResponse>(url, shared),
  );
  return meta.supported_bands?.bands ?? [];
}

export async function loadArchiveMeta(
  signal: AbortSignal,
): Promise<MetaResponse> {
  return metadataRequests("/api/meta", signal, (shared) =>
    fetchJson<MetaResponse>("/api/meta", shared, 8000),
  );
}

export function loadCutout(
  signal: AbortSignal,
  sample: Sample = "scope",
): Promise<Cutout> {
  return sampleRequests(sample, signal, (shared) => fetchSample(sample, shared));
}

async function fetchSample(sample: Sample, signal: AbortSignal): Promise<Cutout> {
  const { metadata, url } = bundledCutouts[sample];
  const response = await fetch(url, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
  });
  if (!response.ok)
    throw new Error(`Cutout request failed (${response.status})`);

  const bytes = await response.arrayBuffer();
  const length = metadata.width * metadata.height;
  if (bytes.byteLength !== length * Float32Array.BYTES_PER_ELEMENT) {
    throw new Error("Cutout size does not match its metadata");
  }

  const input = new DataView(bytes);
  const pixels = new Float32Array(length);
  for (let index = 0; index < length; index++) {
    pixels[index] = input.getFloat32(
      index * Float32Array.BYTES_PER_ELEMENT,
      true,
    );
  }
  return { ...metadata, pixels };
}
