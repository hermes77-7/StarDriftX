import { describe, expect, it } from "vitest";
import scope from "./fixture/scope.json";
import m31 from "./fixture/m31.json";
import {
  defaultNavigation,
  moveView,
  skyAtPoint,
  skyScaleBar,
} from "./navigation";
import { readView, viewerHref } from "./viewState";

describe("sky navigation", () => {
  // Reference values evaluated with Astropy against the original FITS files.
  it.each([
    {
      image: scope,
      x: 7,
      y: 27,
      ra: 150.13795267715685,
      dec: 2.3562290506983303,
    },
    {
      image: m31,
      x: 52,
      y: 218,
      ra: 10.898844704565539,
      dec: 41.39186647458243,
    },
  ])(
    "matches the fixture WCS at the center and after pan and zoom",
    ({ image, x, y, ra, dec }) => {
      const center = skyAtPoint(image, defaultNavigation, { x: 0, y: 0 });
      expect(center?.ra).toBeCloseTo(image.wcs.referenceDegrees[0], 9);
      expect(center?.dec).toBeCloseTo(image.wcs.referenceDegrees[1], 9);

      const anchor = {
        x: (x + 0.5) / image.width - 0.5,
        y: 0.5 - (y + 0.5) / image.height,
      };
      const offset = { x: anchor.x + 0.1, y: anchor.y + 0.2 };
      const zoomed = moveView(defaultNavigation, 2, anchor);
      const panned = moveView(zoomed, 2, anchor, offset);
      for (const result of [
        skyAtPoint(image, defaultNavigation, anchor),
        skyAtPoint(image, zoomed, anchor),
        skyAtPoint(image, panned, offset),
      ]) {
        expect(result?.ra).toBeCloseTo(ra, 9);
        expect(result?.dec).toBeCloseTo(dec, 9);
      }
      expect(skyAtPoint(image, defaultNavigation, { x: 0.6, y: 0 })).toBeNull();
    },
  );

  it("matches angular scale for both fields at fit and after zoom", () => {
    expect(skyScaleBar(36, 360, scope.wcs.degreesPerPixel[0])).toEqual({
      arcseconds: 60,
      width: expect.closeTo(97.5609756, 6),
    });
    expect(skyScaleBar(36, 720, scope.wcs.degreesPerPixel[0])).toEqual({
      arcseconds: 30,
      width: expect.closeTo(97.5609756, 6),
    });
    expect(skyScaleBar(293, 586, m31.wcs.degreesPerPixel[0])).toEqual({
      arcseconds: 300,
      width: expect.closeTo(97.5609756, 6),
    });
  });

  it("restores pan and zoom from a shared view", () => {
    const pan = { x: -0.2, y: 0.4 };
    const url = new URL(
      viewerHref(undefined, 3, "m31", pan),
      "http://localhost",
    );
    expect(readView(url.search)).toMatchObject({
      available: true,
      sample: "m31",
      zoom: 3,
      pan,
    });
  });
});
