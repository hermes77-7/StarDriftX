import { describe, expect, it } from "vitest";
import type { Cutout } from "./cutout";
import { blinkContrast, comparisonIssue, differencePixels } from "./epochs";
import fixture from "./fixture/scope.json";
import { contrastRange, defaultDisplay, drawPixels } from "./image";
import { readView, viewerHref } from "./viewState";

const a: Cutout = {
  ...fixture, width: 3, height: 2, epoch: "2", band: 4,
  pixels: new Float32Array([1, 20, 2, 7, NaN, 1]),
};
const b: Cutout = { ...a, epoch: "1", pixels: new Float32Array([1, 20, 6, 2, 4, NaN]) };

describe("epoch comparison", () => {
  it("keeps the star at the same pixel and uses one brightness scale during blink", () => {
    expect(comparisonIssue(a, b)).toBeNull();
    const contrast = blinkContrast(a.pixels, b.pixels);
    const first = new Uint8ClampedArray(24);
    const second = new Uint8ClampedArray(24);
    drawPixels(a, contrast, defaultDisplay, first);
    drawPixels(b, contrast, defaultDisplay, second);
    expect(first.slice(16, 20)).toEqual(second.slice(16, 20));
    expect(first[16]).toBe(255);
  });

  it("cancels static pixels, preserves residual signs, and masks missing pixels", () => {
    const pixels = differencePixels(a.pixels, b.pixels);
    expect([...pixels]).toEqual([0, 0, -4, 5, NaN, NaN]);
    const reversed = differencePixels(b.pixels, a.pixels);
    expect([...reversed]).toEqual([0, 0, 4, -5, NaN, NaN]);
    const settings = { ...defaultDisplay, stretch: "linear", colormap: "diverging" } as const;
    const residuals = pixels.filter(Number.isFinite).sort();
    const { minimum, span } = contrastRange(residuals, settings);
    expect(minimum + span / 2).toBe(0);
    const rgba = new Uint8ClampedArray(24);
    drawPixels({ ...a, pixels }, residuals, settings, rgba);
    expect([...rgba.slice(4, 12)]).toEqual(Array(8).fill(0));
    expect([...rgba.slice(16, 20)]).toEqual([20, 20, 23, 255]);
    expect(rgba[0]).toBeGreaterThan(rgba[2]);
    expect(rgba[22]).toBeGreaterThan(rgba[20]);
  });

  it("explains incompatibility before subtracting or blinking", () => {
    expect(comparisonIssue(a, { ...b, wcs: { ...b.wcs, referencePixel: [1, 1] } })).toBe("differentGrid");
    expect(comparisonIssue(a, { ...b, band: 5 })).toBe("differentBand");
    expect(comparisonIssue(a, { ...b, wavelengthMicrons: 0.85 })).toBe("differentBand");
    expect(comparisonIssue(a, { ...b, unit: "Jy/sr" })).toBe("differentUnits");
    expect(comparisonIssue(a, { ...b, release: "other" })).toBe("differentRelease");
    expect(comparisonIssue(a, { ...b, pixels: b.pixels.map(() => NaN) })).toBe("noCommonPixels");
  });

  it("restores the ordered difference pair from a shared URL", () => {
    const live = { ra: 150.12, dec: 2.34, fov: 0.06, band: 4, epoch: "4" } as const;
    const href = viewerHref(defaultDisplay, 1.25, "scope", { x: 0, y: 0 }, live, ["4", "2"]);
    expect(readView(new URL(href, "http://localhost").search)).toMatchObject({
      live, difference: ["4", "2"], zoom: 1.25, invalid: false,
    });
    expect(readView("?dif=2-2")).toMatchObject({ difference: null, invalid: true });
  });
});
