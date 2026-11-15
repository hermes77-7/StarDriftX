import { describe, expect, it } from "vitest";
import { parseSkySearch } from "./search";
import { pointAtSky, skyAtPoint } from "./navigation";
import fixture from "./fixture/scope.json";

describe("sky coordinate search", () => {
  it.each(["150.12, +2.34", "150.12 +2.34", "10:00:28.8 +02:20:24", "10h00m28.8s +02d20m24s", "10 00 28.8 +02 20 24"])(
    "recognizes %s", (text) => {
      const result = parseSkySearch(text);
      expect(result).toHaveProperty("position");
      if ("position" in result) {
        expect(result.position.ra).toBeCloseTo(150.12, 9);
        expect(result.position.dec).toBeCloseTo(2.34, 9);
      }
    },
  );

  it("recognizes object names and rejects impossible coordinates", () => {
    expect(parseSkySearch("M31")).toEqual({ name: "M31" });
    expect(parseSkySearch("47 Tuc")).toEqual({ name: "47 Tuc" });
    expect(parseSkySearch("0:00:00 -00:30:00")).toEqual({ position: { ra: 0, dec: -0.5 } });
    expect(parseSkySearch("10:00 +02:30")).toEqual({ position: { ra: 150, dec: 2.5 } });
    expect(parseSkySearch("400, 23")).toEqual({ invalid: true });
    expect(parseSkySearch("10h60m00s +02d00m00s")).toEqual({ invalid: true });
  });

  it("puts catalog coordinates at their FITS pixel centers during pan and zoom", () => {
    const image = { ...fixture, wcs: { ...fixture.wcs, referenceDegrees: [359.999, 75] } };
    const navigation = { zoom: 4, pan: { x: -0.12, y: 0.24 } };
    const x = 9, y = 25;
    const point = {
      x: ((x + 0.5) / image.width - 0.5) * navigation.zoom + navigation.pan.x,
      y: ((y + 0.5) / image.height - 0.5) * navigation.zoom + navigation.pan.y,
    };
    const sky = skyAtPoint(image, navigation, point)!;
    const projected = pointAtSky(image, navigation, sky)!;
    expect(Math.abs(projected.x - point.x) * image.width).toBeLessThan(1e-6);
    expect(Math.abs(projected.y - point.y) * image.height).toBeLessThan(1e-6);
  });
});
