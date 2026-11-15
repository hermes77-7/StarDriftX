import type { CutoutMetadata } from "./cutout";

export type Point = { x: number; y: number };
export type Navigation = { zoom: number; pan: Point };

export const minZoom = 1;
export const maxZoom = 8;
export const defaultNavigation: Navigation = {
  zoom: minZoom,
  pan: { x: 0, y: 0 },
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function clampPan(pan: Point, zoom: number): Point {
  const limit = zoom / 2;
  return { x: clamp(pan.x, -limit, limit), y: clamp(pan.y, -limit, limit) };
}

// Points and pan offsets are fractions of the unzoomed image, measured from its center.
export function moveView(
  view: Navigation,
  targetZoom: number,
  from: Point = { x: 0, y: 0 },
  to: Point = from,
): Navigation {
  const zoom = clamp(targetZoom, minZoom, maxZoom);
  const ratio = zoom / view.zoom;
  return {
    zoom,
    pan: clampPan(
      {
        x: to.x - (from.x - view.pan.x) * ratio,
        y: to.y - (from.y - view.pan.y) * ratio,
      },
      zoom,
    ),
  };
}

export function skyAtPoint(
  { width, height, wcs }: Pick<CutoutMetadata, "width" | "height" | "wcs">,
  view: Navigation,
  point: Point,
) {
  const x = (point.x - view.pan.x) / view.zoom + 0.5;
  const y = (point.y - view.pan.y) / view.zoom + 0.5;
  if (x < 0 || x >= 1 || y < 0 || y >= 1) return null;

  // Canvas starts at the upper-left pixel edge. FITS counts pixel centers from one,
  // with the vertical axis increasing from the bottom for these north-up images.
  const fitsX = x * width + 0.5;
  const fitsY = (1 - y) * height + 0.5;
  const radians = Math.PI / 180;
  const xi = (fitsX - wcs.referencePixel[0]) * wcs.degreesPerPixel[0] * radians;
  const eta =
    (fitsY - wcs.referencePixel[1]) * wcs.degreesPerPixel[1] * radians;
  const dec0 = wcs.referenceDegrees[1] * radians;
  const denominator = Math.cos(dec0) - eta * Math.sin(dec0);
  // Invert the FITS TAN (gnomonic) projection around the reference position.
  const ra = wcs.referenceDegrees[0] + Math.atan2(xi, denominator) / radians;
  const dec =
    Math.atan2(
      Math.sin(dec0) + eta * Math.cos(dec0),
      Math.hypot(denominator, xi),
    ) / radians;
  return { ra: (ra + 360) % 360, dec };
}

const scaleSteps = [
  1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 180, 300, 600, 900, 1200, 1800, 3600,
];

export function pointAtSky(
  { width, height, wcs }: Pick<CutoutMetadata, "width" | "height" | "wcs">,
  view: Navigation,
  coordinates: { ra: number; dec: number },
): Point | null {
  const radians = Math.PI / 180;
  const delta = (coordinates.ra - wcs.referenceDegrees[0]) * radians;
  const dec = coordinates.dec * radians;
  const dec0 = wcs.referenceDegrees[1] * radians;
  const cosc = Math.sin(dec0) * Math.sin(dec) + Math.cos(dec0) * Math.cos(dec) * Math.cos(delta);
  if (cosc <= 0) return null;
  const xi = Math.cos(dec) * Math.sin(delta) / cosc;
  const eta = (Math.cos(dec0) * Math.sin(dec) - Math.sin(dec0) * Math.cos(dec) * Math.cos(delta)) / cosc;
  const x = (wcs.referencePixel[0] + xi / (radians * wcs.degreesPerPixel[0]) - 0.5) / width;
  const y = 1 - (wcs.referencePixel[1] + eta / (radians * wcs.degreesPerPixel[1]) - 0.5) / height;
  if (!(x >= 0 && x < 1 && y >= 0 && y < 1)) return null;
  return { x: (x - 0.5) * view.zoom + view.pan.x, y: (y - 0.5) * view.zoom + view.pan.y };
}

export function skyScaleBar(
  imageWidth: number,
  displayedWidth: number,
  degreesPerPixel: number,
) {
  if (displayedWidth <= 0) return null;
  const arcsecPerCssPixel =
    (Math.abs(degreesPerPixel) * 3600 * imageWidth) / displayedWidth;
  const arcseconds =
    scaleSteps.findLast((step) => step <= 100 * arcsecPerCssPixel) ?? 1;
  return { arcseconds, width: arcseconds / arcsecPerCssPixel };
}
