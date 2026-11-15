import type { Cutout } from "./cutout";

export const numberedEpochs = ["1", "2", "3", "4"] as const;
export type NumberedEpoch = (typeof numberedEpochs)[number];
export type EpochPair = readonly [NumberedEpoch, NumberedEpoch];

export function isNumberedEpoch(value: string): value is NumberedEpoch {
  return numberedEpochs.some((epoch) => epoch === value);
}

export function comparisonIssue(a: Cutout, b: Cutout, difference = true) {
  if (
    a.width !== b.width ||
    a.height !== b.height ||
    a.wcs.frame !== b.wcs.frame ||
    a.wcs.projection !== b.wcs.projection ||
    a.wcs.referenceDegrees.some((value, axis) => value !== b.wcs.referenceDegrees[axis]) ||
    a.wcs.referencePixel.some((value, axis) => value !== b.wcs.referencePixel[axis]) ||
    a.wcs.degreesPerPixel.some((value, axis) => value !== b.wcs.degreesPerPixel[axis])
  ) return "differentGrid";
  if (a.band !== b.band || a.detector !== b.detector || a.channel !== b.channel) return "differentBand";
  if (a.band === undefined) {
    if (difference) return "differenceNeedsBand";
  } else if (
    !Number.isFinite(a.wavelengthMicrons) || !Number.isFinite(b.wavelengthMicrons) ||
    Math.abs(a.wavelengthMicrons - b.wavelengthMicrons) > 0.000001
  ) return "differentBand";
  if (a.unit !== b.unit) return "differentUnits";
  if (a.release !== b.release) return "differentRelease";
  if (!a.pixels.some((value, index) => Number.isFinite(value) && Number.isFinite(b.pixels[index]))) {
    return "noCommonPixels";
  }
  return null;
}

export function differencePixels(a: Float32Array, b: Float32Array) {
  return a.map((value, index) =>
    Number.isFinite(value) && Number.isFinite(b[index]) ? value - b[index] : NaN,
  );
}

export function blinkContrast(a: Float32Array, b: Float32Array) {
  const values = new Float32Array(a.length * 2);
  values.set(a);
  values.set(b, a.length);
  return values.filter(Number.isFinite).sort();
}
