import { bandCount, defaultFovLimits, detectorCount, getSampleView, sampleView } from "./cutout";
import type { LiveView, Sample, SpectralSelection } from "./cutout";
import type { Epoch } from "../types/api";
import { isNumberedEpoch } from "./epochs";
import type { EpochPair } from "./epochs";
import {
  clipStep,
  defaultDisplay,
  defaultStrength,
  strengthRange,
} from "./image";
import type { DisplaySettings } from "./image";
import { clampPan, maxZoom, minZoom } from "./navigation";
import type { Point } from "./navigation";

const viewKeys = [
  "ra",
  "dec",
  "fov",
  "band",
  "detector",
  "epoch",
  "mode",
  "dif",
  "stretch",
  "low",
  "high",
  "strength",
  "colormap",
  "background",
  "zoom",
  "panX",
  "panY",
  "sample",
  "source",
];

export function isViewerLocation(pathname: string, search: string) {
  return (
    /^\/(?:viewer|explore)\/?$/.test(pathname) ||
    (pathname === "/" &&
      viewKeys.some((key) => new URLSearchParams(search).has(key)))
  );
}

export function readView(search: string) {
  const params = new URLSearchParams(search);
  let invalid = false;

  function readNumber(
    key: string,
    fallback: number,
    min: number,
    max: number,
    integer = false,
  ) {
    const raw = params.get(key);
    if (raw === null) return fallback;
    const value = Number(raw);
    if (
      !raw.trim() ||
      !Number.isFinite(value) ||
      value < min ||
      value > max ||
      (integer && !Number.isInteger(value))
    ) {
      invalid = true;
      return fallback;
    }
    return value;
  }

  const requested = params.get("sample");
  const m31View = getSampleView("m31");
  const defaults = requested === "m31" ? m31View : sampleView;
  const ra = readNumber("ra", defaults.ra, 0, 360);
  const dec = readNumber("dec", defaults.dec, -90, 90);
  const sample: Sample =
    requested === "m31" ||
    (requested === null && ra % 360 === m31View.ra && dec === m31View.dec)
      ? "m31"
      : "scope";
  const view = getSampleView(sample);
  const requestedFov = readNumber(
    "fov",
    view.fov,
    Number.EPSILON,
    Number.MAX_VALUE,
  );
  const fov = Math.max(
    defaultFovLimits.min_deg,
    Math.min(defaultFovLimits.max_deg, requestedFov),
  );
  if (fov !== requestedFov) invalid = true;
  const mode = params.get("mode") ?? "single";
  if (mode !== "single" && mode !== "stack") invalid = true;
  const selection: SpectralSelection = mode === "stack"
    ? { mode: "stack", detector: readNumber("detector", 1, 1, detectorCount, true) }
    : { band: readNumber("band", view.band, 1, bandCount, true) };
  if (mode === "stack" && (params.has("band") || !params.has("detector"))) invalid = true;
  const epochValue = params.get("epoch") ?? "all";
  const isEpoch = epochValue === "all" || isNumberedEpoch(epochValue);
  if (!isEpoch) invalid = true;
  const epoch: Epoch = isEpoch ? epochValue : "all";
  const dif = params.get("dif");
  const [a, b, extra] = (dif ?? "").split("-");
  const difference: EpochPair | null =
    mode !== "stack" && a !== b && extra === undefined && isNumberedEpoch(a) && isNumberedEpoch(b)
      ? [a, b]
      : null;
  if (dif !== null && !difference) invalid = true;
  const stretch = params.get("stretch") ?? defaultDisplay.stretch;
  const supportedStretch =
    stretch === "linear" || stretch === "log" || stretch === "asinh";
  if (!supportedStretch) invalid = true;
  const stretchType = supportedStretch ? stretch : defaultDisplay.stretch;
  const low = readNumber("low", defaultDisplay.low, 0, 100 - clipStep);
  const high = readNumber("high", defaultDisplay.high, clipStep, 100);
  const validClip = high - low >= clipStep;
  const colormap = params.get("colormap") ?? defaultDisplay.colormap;
  const background = params.get("background");
  if (
    !validClip ||
    (colormap !== "grayscale" && colormap !== "viridis") ||
    (background !== null && background !== "1" && background !== "0")
  )
    invalid = true;
  const display: DisplaySettings = {
    stretch: stretchType,
    low: validClip ? low : defaultDisplay.low,
    high: validClip ? high : defaultDisplay.high,
    strength: readNumber(
      "strength",
      defaultStrength[stretchType],
      strengthRange.min,
      strengthRange.max,
      true,
    ),
    colormap: colormap === "viridis" ? "viridis" : defaultDisplay.colormap,
    subtractBackground: background === "1",
  };

  const hasBundledPosition =
    (requested === null || requested === sample) &&
    ra % 360 === view.ra &&
    dec === view.dec;
  const available =
    params.get("source") !== "live" &&
    hasBundledPosition &&
    Math.abs(fov - view.fov) < 0.0000005 &&
    selection.mode !== "stack" && (selection.band === view.band || requested === "m31") &&
    !params.has("epoch") &&
    !difference;

  const zoom = readNumber("zoom", minZoom, minZoom, maxZoom);
  const pan = {
    x: readNumber("panX", 0, -zoom / 2, zoom / 2),
    y: readNumber("panY", 0, -zoom / 2, zoom / 2),
  };
  return {
    available,
    live: !available ? { ra: ra % 360, dec, fov, epoch, ...selection } : null,
    hasBundledPosition,
    difference,
    invalid,
    display,
    zoom,
    pan,
    sample,
    bundledBand: available ? selection.band : view.band,
    fov,
    selection,
    coordinates: { ra: ra % 360, dec },
  };
}

export function viewerHref(
  display = defaultDisplay,
  zoom = 1,
  sample: Sample = "scope",
  pan: Point = { x: 0, y: 0 },
  live?: LiveView | null,
  difference?: EpochPair | null,
  bundledBand = getSampleView(sample).band,
) {
  const view = live ?? getSampleView(sample);
  const params = new URLSearchParams({
    ra: String(view.ra),
    dec: String(view.dec),
    fov: String(view.fov),
    stretch: display.stretch,
    low: String(display.low),
    high: String(display.high),
  });
  if (live?.mode === "stack") {
    params.set("mode", "stack");
    params.set("detector", String(live.detector));
  } else params.set("band", String(live?.band ?? bundledBand));
  if (display.strength !== defaultStrength[display.stretch]) {
    params.set("strength", String(display.strength));
  }
  if (display.colormap !== defaultDisplay.colormap)
    params.set("colormap", display.colormap);
  if (display.subtractBackground) params.set("background", "1");
  if (live) {
    params.set("source", "live");
    if (live.epoch !== "all") params.set("epoch", live.epoch);
    if (difference && live.mode !== "stack") params.set("dif", difference.join("-"));
  } else if (sample !== "scope") params.set("sample", sample);
  if (zoom !== 1) params.set("zoom", String(zoom));
  const offset = clampPan(pan, zoom);
  if (offset.x !== 0) params.set("panX", String(offset.x));
  if (offset.y !== 0) params.set("panY", String(offset.y));
  return `/viewer?${params}`;
}
