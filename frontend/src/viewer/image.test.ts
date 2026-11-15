import { describe, expect, it } from "vitest";
import fixture from "./fixture/scope.json";
import {
  defaultDisplay,
  drawPixels,
  subtractBackgroundGradient,
} from "./image";
import { readView, viewerHref } from "./viewState";

describe("cutout rendering", () => {
  it("flips FITS rows once, keeps east on the left, and leaves missing pixels transparent", () => {
    expect(fixture.wcs.degreesPerPixel[0]).toBeLessThan(0);
    expect(fixture.wcs.degreesPerPixel[1]).toBeGreaterThan(0);
    const cutout = {
      ...fixture,
      width: 2,
      height: 2,
      pixels: new Float32Array([0, NaN, 10, 5]),
    };
    const frame = new Uint8ClampedArray(16);
    drawPixels(
      cutout,
      new Float32Array([0, 5, 10]),
      {
        ...defaultDisplay,
        stretch: "linear",
        low: 0,
        high: 100,
      },
      frame,
    );
    expect([...frame]).toEqual([
      255, 255, 255, 255, 128, 128, 128, 255, 0, 0, 0, 255, 0, 0, 0, 0,
    ]);
  });

  it("removes a planar gradient while preserving a source and missing pixels", () => {
    const pixels = Float32Array.from(
      { length: 49 },
      (_, index) => 10 + 2 * (index % 7) - 3 * Math.floor(index / 7),
    );
    pixels[0] = NaN;
    pixels[24] += 30;
    const original = pixels.slice();
    const corrected = subtractBackgroundGradient(pixels, 7, 7);
    expect(corrected[0]).toBeNaN();
    expect(corrected[24]).toBe(30);
    expect(
      [...corrected].filter(Number.isFinite).filter((value) => value !== 0),
    ).toEqual([30]);
    expect(pixels).toEqual(original);
  });
});

describe("viewer links", () => {
  it("round-trips settings and rejects unsupported requests", () => {
    const settings = {
      stretch: "log",
      low: 4,
      high: 97,
      strength: 70,
      colormap: "viridis",
      subtractBackground: true,
    } as const;
    const search = new URL(viewerHref(settings, 1.25), "http://localhost")
      .search;
    expect(readView(search)).toMatchObject({
      display: settings,
      zoom: 1.25,
      available: true,
      invalid: false,
    });
    expect(readView("?ra=invalid&stretch=bogus")).toMatchObject({
      available: true,
      invalid: true,
    });
    expect(readView("?epoch=3").available).toBe(false);
    expect(readView("?stretch=log").display.strength).toBe(100);
    expect(readView("?low=95&high=20")).toMatchObject({
      display: defaultDisplay,
      invalid: true,
    });
  });
});
