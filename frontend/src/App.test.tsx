import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { decodePayload } from "./utils/payload";
import App from "./App";
import type { CutoutBinaryPayload } from "./types/api";
import m31Fixture from "./viewer/fixture/m31.json";
import { viewerHref } from "./viewer/viewState";

vi.mock("./utils/payload", { spy: true });

const contextCamera = vi.hoisted(() => ({
  fov: 0,
  ra: 0,
  dec: 0,
  listeners: new Map<string, (() => void) | null>(),
}));

vi.mock("aladin-lite", () => ({
  default: {
    init: Promise.resolve(),
    aladin: (_host: HTMLDivElement, options: {
      target: string; fov: number; survey: { successCallback?: () => void };
    }) => {
      [contextCamera.ra, contextCamera.dec] = options.target.split(" ").map(Number);
      contextCamera.fov = options.fov;
      options.survey.successCallback?.();
      return {
        getRaDec: () => [contextCamera.ra, contextCamera.dec],
        getFov: () => [contextCamera.fov, contextCamera.fov],
        gotoRaDec: (ra: number, dec: number) => Object.assign(contextCamera, { ra, dec }),
        setFoV: (fov: number) => { contextCamera.fov = fov; },
        setFoVRange: () => {},
        setBaseImageLayer: (layer: { successCallback?: () => void }) => layer.successCallback?.(),
        on: (event: string, callback: (() => void) | null) => contextCamera.listeners.set(event, callback),
      };
    },
    HiPS: () => ({}),
  },
}));

function epochPayload(epoch: CutoutBinaryPayload["header"]["epoch"], pixels: number[], scale = 0.001708): CutoutBinaryPayload {
  const header = {
    source: "irsa", version: "1.0", data_release: "test-epochs", epoch, band: 4,
    detector: 1, channel: 4, wavelength_um: 0.809, bunit: "MJy/sr", nexp: 1,
    photometric_normalized: false,
    wcs: {
      radesys: "ICRS", ctype: ["RA---TAN", "DEC--TAN"], shape: [2, 2],
      crval: [80, 15], crpix: [1.5, 1.5], cdelt: [-scale, scale],
    },
  } satisfies CutoutBinaryPayload["header"];
  return { header, data: new Float32Array(pixels) };
}

let testTime = Date.now();
beforeEach(() => {
  testTime += 120000;
  vi.spyOn(Date, "now").mockReturnValue(testTime);
  window.history.replaceState(null, "", "/");
  localStorage.clear();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Star Drift", () => {
  it("routes the landing call to action and Explore link to the real viewer", async () => {
    const { unmount } = render(<App />);
    const href = screen.getByRole("link", { name: "Explore now" }).getAttribute("href")!;
    expect(new URL(href, window.location.origin).pathname).toBe("/explore");
    expect(screen.getByRole("link", { name: "Explore" }).getAttribute("href")).toBe(href);
    unmount();

    window.history.replaceState(null, "", href);
    vi.stubGlobal("fetch", vi.fn(async () =>
      Response.json({ error: { code: "NO_COVERAGE" } }, { status: 404 }),
    ));
    render(<App />);
    expect(screen.getByRole("heading", { name: "Sky explorer" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Share view" })).toBeTruthy();
    await screen.findByRole("heading", { name: "No coverage for this selection" });
  });

  it("changes language and theme with one click and remembers both", () => {
    const { unmount } = render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Switch to French" }));
    fireEvent.click(screen.getByRole("button", { name: "Mode clair" }));
    expect(
      screen
        .getByRole("link", { name: "Ouvrir l’explorateur" })
        .getAttribute("href"),
    ).toContain("source=live");
    expect(document.documentElement.lang).toBe("fr");
    expect(document.documentElement.dataset.theme).toBe("light");

    unmount();
    render(<App />);
    expect(screen.getByRole("button", { name: "Mode sombre" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Passer à l’anglais" }),
    ).toBeTruthy();
  });

  it("opens a direct view and redraws its float pixels without refetching", async () => {
    window.history.replaceState(null, "", viewerHref());
    const bytes = new ArrayBuffer(36 * 36 * 4);
    const values = new DataView(bytes);
    for (let index = 0; index < 36 * 36; index++)
      values.setFloat32(index * 4, index, true);
    const fetchCutout = vi.fn().mockResolvedValue(new Response(bytes));
    vi.stubGlobal("fetch", fetchCutout);
    let drawnPixels: Uint8ClampedArray | undefined;
    const putImageData = vi.fn((frame: ImageData) => {
      drawnPixels = frame.data.slice();
    });
    const context = {
      createImageData: (width: number, height: number) => ({
        width,
        height,
        data: new Uint8ClampedArray(width * height * 4),
      }),
      putImageData,
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      (kind) => (kind === "2d" ? context : null),
    );

    const { unmount } = render(<App />);
    await screen.findByRole("img", { name: /Grayscale SPHEREx image/ });
    expect(
      screen
        .getByRole("img", { name: /Grayscale SPHEREx image/ })
        .getAttribute("data-renderer"),
    ).toBe("canvas2d");
    const original = drawnPixels;
    fireEvent.change(screen.getByRole("slider", { name: "Black point" }), {
      target: { value: "15" },
    });
    expect(drawnPixels).not.toEqual(original);
    const afterBlackPoint = drawnPixels;
    fireEvent.change(screen.getByRole("slider", { name: "White point" }), {
      target: { value: "80" },
    });
    expect(drawnPixels).not.toEqual(afterBlackPoint);
    fireEvent.change(screen.getByLabelText("Stretch"), {
      target: { value: "log" },
    });
    const logDefault = drawnPixels;
    fireEvent.change(screen.getByRole("slider", { name: "Stretch strength" }), {
      target: { value: "20" },
    });
    expect(drawnPixels).not.toEqual(logDefault);
    fireEvent.change(screen.getByLabelText("Stretch"), {
      target: { value: "linear" },
    });
    expect(drawnPixels).not.toEqual(original);
    expect(window.location.search).toContain("stretch=linear");
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(window.location.search).toContain("zoom=1.25");
    expect(fetchCutout).toHaveBeenCalledTimes(1);

    fetchCutout.mockImplementation(() => Promise.resolve(new Response(bytes)));
    unmount();
    render(<App />);
    const canvas = await screen.findByRole("img", {
      name: /Grayscale SPHEREx image/,
    });
    expect((canvas as HTMLElement).style.transform).toContain("scale(1.25)");
    expect((screen.getByLabelText("Stretch") as HTMLSelectElement).value).toBe(
      "linear",
    );
  });

  it("cancels stale live requests and displays the current coverage error", async () => {
    window.history.replaceState(null, "", "/viewer?ra=80&dec=15&fov=0.2");
    const requests: {
      url: string;
      signal: AbortSignal;
      reply: (response: Response) => void;
    }[] = [];
    const fetchMock = vi.fn((url: string, init: RequestInit) => {
      if (url.startsWith("/api/meta"))
        return Promise.resolve(
          Response.json({
            fov_limits: { min_deg: 0.05, max_deg: 1, default_deg: 0.2 },
            pixel_scale_arcsec: 6.15,
          }),
        );
      return new Promise<Response>((reply) =>
        requests.push({ url, signal: init.signal!, reply }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].url).toContain("ra=80");
    fireEvent.change(screen.getByLabelText("RA (degrees)"), {
      target: { value: "81" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Load sky field" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[0].signal.aborted).toBe(true);
    expect(requests[1].url).toContain("ra=81");
    requests[1].reply(
      Response.json({ error: { code: "NO_COVERAGE" } }, { status: 404 }),
    );
    await screen.findByRole("heading", {
      name: "No coverage for this selection",
    });
    requests[0].reply(
      Response.json({ error: { code: "UPSTREAM_TIMEOUT" } }, { status: 504 }),
    );
    expect(
      screen.getByRole("heading", { name: "No coverage for this selection" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit position" }));
    expect(document.activeElement).toBe(screen.getByLabelText("RA (degrees)"));

    const slider = screen.getByRole("slider", { name: "Adjust field of view" });
    fireEvent.change(slider, { target: { value: "0.23" } });
    fireEvent.change(slider, { target: { value: "0.25" } });
    expect(requests).toHaveLength(2);
    await waitFor(() => expect(requests).toHaveLength(3), { timeout: 3000 });
    expect(requests[2].url).toContain("fov=0.25");
  });

  it("switches prefetched epochs, blinks on one canvas, and restores a difference pair", { timeout: 15000 }, async () => {
    window.history.replaceState(null, "", "/viewer?ra=80&dec=15&fov=0.2&epoch=2&zoom=1.25");
    const requested: string[] = [];
    const images: Record<string, number[]> = {
      "1": [20, 2, 1, 1], "2": [20, 7, 1, 1], "3": [20, 3, 1, 1],
    };
    vi.mocked(decodePayload).mockImplementation(async (bytes) => {
      const epoch = new TextDecoder().decode(bytes) as "1" | "2" | "3";
      return epochPayload(epoch, images[epoch], epoch === "3" ? 0.002 : undefined);
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/meta")) return Response.json({ epochs: [{ id: "1" }, { id: "2" }, { id: "3" }] });
      const epoch = new URL(url, window.location.origin).searchParams.get("epoch")!;
      requested.push(epoch);
      return new Response(epoch);
    }));
    const context = {
      createImageData: (width: number, height: number) => ({
        width, height, data: new Uint8ClampedArray(width * height * 4),
      }),
      putImageData: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      (kind) => (kind === "2d" ? context : null),
    );

    render(<App />);
    await waitFor(() => expect(requested).toEqual(["2", "1"]), { timeout: 3000 });
    fireEvent.click(screen.getByRole("button", { name: "Epoch 1" }));
    await waitFor(() => expect(document.querySelector(".stage-label")?.textContent).toContain("Epoch 1"), { timeout: 3000 });
    fireEvent.change(screen.getByLabelText("Time per frame"), { target: { value: "0.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Start blink" }));
    await waitFor(() => expect(document.querySelector(".stage-label")?.textContent).toContain("Epoch 2"));
    const canvas = screen.getByRole("img", { name: /Grayscale SPHEREx image/ });
    await waitFor(() => expect(document.querySelector(".stage-label")?.textContent).toContain("Epoch 1"), { timeout: 3000 });
    expect(screen.getByRole("img", { name: /Grayscale SPHEREx image/ })).toBe(canvas);
    expect((canvas as HTMLElement).style.transform).toContain("scale(1.25)");

    fireEvent.click(screen.getByRole("button", { name: "Difference" }));
    await screen.findByRole("img", { name: "Signed difference between SPHEREx epochs" });
    expect(screen.getByText("Photometric normalization not applied")).toBeTruthy();
    expect(screen.getByText("+ Epoch 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Swap A and B" }));
    expect(window.location.search).toContain("dif=1-2");
    expect(screen.getByText("+ Epoch 1")).toBeTruthy();
    expect(requested).toEqual(["2", "1"]);

    fireEvent.change(screen.getByLabelText("Epoch A · positive"), { target: { value: "3" } });
    expect((await screen.findByRole("alert")).textContent).toContain("different sky grids");
    expect(requested).toEqual(["2", "1", "3"]);
    expect(screen.queryByRole("img", { name: "Signed difference between SPHEREx epochs" })).toBeNull();
  });

  it("fetches just the final Sky Context camera when returning to Image", async () => {
    window.history.replaceState(null, "", viewerHref());
    contextCamera.listeners.clear();
    const cutouts: URLSearchParams[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/meta")) return Response.json({ epochs: [] });
      if (url.startsWith("/api/cutout?")) {
        cutouts.push(new URL(url, window.location.origin).searchParams);
        return Response.json({ error: { code: "NO_COVERAGE" } }, { status: 404 });
      }
      return new Response(new ArrayBuffer(36 * 36 * 4));
    }));
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sky Context" }));
    await screen.findByText("Open Image or Difference to load SPHEREx for this field.");
    Object.assign(contextCamera, { ra: 152.1, dec: 1.8, fov: 0.8 });
    contextCamera.listeners.get("positionChanged")?.();
    await new Promise((resolve) => setTimeout(resolve, 200));
    Object.assign(contextCamera, { ra: 153, dec: 1.9, fov: 0.5 });
    contextCamera.listeners.get("zoomChanged")?.();
    expect(cutouts).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Image" }));
    await screen.findByRole("heading", { name: "No coverage for this selection" });
    expect(cutouts).toHaveLength(1);
    expect(cutouts[0].get("ra")).toBe("153");
    expect(cutouts[0].get("dec")).toBe("1.9");
    expect(cutouts[0].get("fov")).toBe("0.5");
  });

  it("debounces bands and restores a detector stack from the URL", { timeout: 15000 }, async () => {
    window.history.replaceState(null, "", "/viewer?ra=80&dec=15&fov=0.2&epoch=4&band=4");
    const requests: URLSearchParams[] = [];
    vi.mocked(decodePayload).mockImplementation(async (bytes) => {
      const params = new URLSearchParams(new TextDecoder().decode(bytes));
      const product = epochPayload("4", [1, 3, 4, 6]);
      if (params.get("mode") === "stack") {
        delete product.header.band;
        delete product.header.channel;
        product.header.detector = Number(params.get("detector"));
        product.header.wavelength_range_um = [3.8, 4.4];
        product.header.nexp = 12;
      } else {
        product.header.band = Number(params.get("band"));
        product.header.channel = product.header.band;
        product.header.wavelength_um = product.header.band === 5 ? 0.828 : 0.809;
      }
      return product;
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/meta")) return Response.json({
        epochs: [{ id: "4" }], data_release: "test-epochs",
        supported_bands: {
          bands: [
            { band: 4, detector: 1, channel: 4, wavelength_um: 0.809 },
            { band: 5, detector: 1, channel: 5, wavelength_um: 0.828 },
          ],
        },
      });
      const params = new URL(url, window.location.origin).searchParams;
      requests.push(params);
      return new Response(params.toString(), { headers: { ETag: '"verified"' } });
    }));
    const context = {
      createImageData: (width: number, height: number) => ({
        width, height, data: new Uint8ClampedArray(width * height * 4),
      }),
      putImageData: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      (kind) => (kind === "2d" ? context : null),
    );

    render(<App />);
    await screen.findByRole("img", { name: /Grayscale SPHEREx image/ });
    const slider = screen.getByRole("slider", { name: "Band" });
    fireEvent.change(slider, { target: { value: "7" } });
    fireEvent.change(slider, { target: { value: "5" } });
    expect(requests).toHaveLength(1);
    await waitFor(() => expect(requests.at(-1)?.get("band")).toBe("5"), { timeout: 3000 });
    await waitFor(() => expect(document.querySelector(".metadata")?.textContent).toContain("0.828 µm"));
    expect(requests.map((item) => item.get("band"))).toEqual(["4", "5"]);
    fireEvent.change(screen.getByRole("slider", { name: "Band" }), { target: { value: "4" } });
    await waitFor(() => expect(document.querySelector(".metadata")?.textContent).toContain("0.809 µm"), {
      timeout: 3000,
    });
    expect(requests.map((item) => item.get("band"))).toEqual(["4", "5"]);

    fireEvent.click(screen.getByRole("button", { name: "Detector stack" }));
    fireEvent.click(screen.getByRole("button", { name: "Detector 5" }));
    await waitFor(() => expect(requests.at(-1)?.get("detector")).toBe("5"));
    expect(requests.at(-1)?.get("mode")).toBe("stack");
    expect(requests.at(-1)?.has("band")).toBe(false);
    const share = document.querySelector<HTMLInputElement>(".share-dialog input")!.value;
    const parsed = new URL(share);
    expect(parsed.searchParams.get("detector")).toBe("5");
    expect(parsed.searchParams.get("mode")).toBe("stack");
    expect(parsed.searchParams.has("band")).toBe(false);
    expect(window.location.search).toContain("epoch=4");
    await waitFor(() => expect(document.querySelector(".metadata")?.textContent).toContain("Exposures combined12"));
    cleanup();
    window.history.replaceState(null, "", share);
    render(<App />);
    await waitFor(() => expect(document.querySelector(".stage-label")?.textContent).toContain("Detector 5 stack"));
  });

  it("loads M31 with its own pixels and keeps both sample links available", async () => {
    const href = viewerHref(undefined, 1, "m31");
    render(<App />);
    expect(
      screen
        .getByRole("link", { name: /Andromeda Galaxy/ })
        .getAttribute("href"),
    ).toBe(href);
    cleanup();
    window.history.replaceState(null, "", href + "&panX=0.2&unrelated=discard");

    const bytes = new ArrayBuffer(293 * 293 * 4);
    const fetchCutout = vi.fn(async (url: string) =>
      url.endsWith("manifest.json") ? new Response(null, { status: 404 }) : new Response(bytes),
    );
    vi.stubGlobal("fetch", fetchCutout);
    const context = {
      createImageData: (width: number, height: number) => ({
        data: new Uint8ClampedArray(width * height * 4),
      }),
      putImageData: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      (kind) => (kind === "2d" ? context : null),
    );

    render(<App />);
    const image = await screen.findByRole("img", {
      name: /Grayscale SPHEREx image/,
    });
    expect(image.getAttribute("width")).toBe("293");
    expect(fetchCutout.mock.calls[1][0]).toContain("m31.f32");
    expect(screen.queryByText("Observation time (UTC)")).toBeNull();
    expect(
      screen.getByRole("link", { name: "Original field" }).getAttribute("href"),
    ).toBe(viewerHref());
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(window.location.search).toContain("sample=m31");
    expect(window.location.search).toContain("zoom=1.25");
    expect(fetchCutout).toHaveBeenCalledTimes(2);

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    // jsdom does not implement native modal dialogs.
    const showModal = Object.getOwnPropertyDescriptor(
      HTMLDialogElement.prototype,
      "showModal",
    );
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value(this: HTMLDialogElement) {
        this.open = true;
      },
    });
    try {
      fireEvent.click(screen.getByRole("button", { name: "Share view" }));
      const dialogElement = screen.getByRole<HTMLDialogElement>("dialog");
      const initialDialog = within(dialogElement);
      const initialUrl = new URL(
        (initialDialog.getByLabelText("View link") as HTMLInputElement).value,
      );
      expect(initialUrl.searchParams.has("unrelated")).toBe(false);
      dialogElement.open = false;
      fireEvent.change(screen.getByLabelText("Colormap"), {
        target: { value: "viridis" },
      });
      fireEvent.click(
        screen.getByRole("checkbox", { name: "Subtract background gradient" }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Share view" }));
      const dialog = within(screen.getByRole("dialog"));
      const shareUrl = (dialog.getByLabelText("View link") as HTMLInputElement)
        .value;
      fireEvent.click(dialog.getByRole("button", { name: "Copy link" }));
      await waitFor(() => expect(writeText).toHaveBeenCalledWith(shareUrl));
      const params = new URL(shareUrl).searchParams;
      expect(params.get("sample")).toBe("m31");
      expect(params.get("zoom")).toBe("1.25");
      expect(Number(params.get("panX"))).toBeCloseTo(0.25);
      expect(params.get("colormap")).toBe("viridis");
      expect(params.get("background")).toBe("1");
    } finally {
      Reflect.deleteProperty(navigator, "clipboard");
      if (showModal)
        Object.defineProperty(
          HTMLDialogElement.prototype,
          "showModal",
          showModal,
        );
      else Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
    }
  });

  it("labels the DR1 cube fallback and can return to live data", async () => {
    window.history.replaceState(null, "", "/viewer?ra=80&dec=15&fov=0.2&epoch=all");
    let live = false;
    vi.mocked(decodePayload).mockImplementation(async () => {
      const payload = epochPayload("all", [1, 2, 3, 4], 12.9 / 3600);
      if (!live) {
        payload.header.source = "dr1-cube";
        payload.header.data_release = "spherex_dr1";
      }
      return payload;
    });
    const requests = vi.fn(async (url: string) => {
      if (url.startsWith("/api/meta")) return Response.json({ epochs: [] });
      return new Response("cutout", { headers: { "Cache-Control": "no-store" } });
    });
    vi.stubGlobal("fetch", requests);
    const context = {
      createImageData: (width: number, height: number) => ({
        data: new Uint8ClampedArray(width * height * 4),
      }),
      putImageData: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      (kind) => (kind === "2d" ? context : null),
    );

    render(<App />);
    const notice = await screen.findByText(/Live cutout unavailable/);
    expect(screen.getByText("Surveys 1 and 2 combined", { selector: "dd" })).toBeTruthy();
    live = true;
    fireEvent.click(within(notice).getByRole("button", { name: "Try live again" }));
    await screen.findByText("test-epochs", { selector: "dd" });
    expect(screen.queryByText(/Live cutout unavailable/)).toBeNull();
    expect(requests.mock.calls.some(([url]) => url.includes("wait_for_live=true"))).toBe(true);
  });

  it("loads and shares bundled M31 bands without contacting the live API", async () => {
    window.history.replaceState(null, "", viewerHref(undefined, 1, "m31"));
    const bands = [
      { band: 4, detector: 1, channel: 4, wavelength_um: 0.809, file: "band-004-0000000000000004.spx1" },
      { band: 53, detector: 4, channel: 2, wavelength_um: 2.466, file: "band-053-0000000000000053.spx1" },
    ];
    vi.mocked(decodePayload).mockImplementation(async (bytes) => {
      const band = bands.find(({ band }) => band === new Uint8Array(bytes)[0])!;
      const { wcs, width, height } = m31Fixture;
      return {
        header: {
          ...band, version: "1.0", source: "m31-dr1", data_release: "spherex_dr1",
          bunit: "MJy/sr",
          wcs: {
            radesys: "ICRS", ctype: ["RA---TAN", "DEC--TAN"], shape: [height, width],
            crval: wcs.referenceDegrees as [number, number],
            crpix: wcs.referencePixel as [number, number],
            cdelt: wcs.degreesPerPixel as [number, number],
          },
        },
        data: new Float32Array(width * height).fill(band.band),
      };
    });
    const requests = vi.fn(async (url: string) => {
      if (url.endsWith("manifest.json")) return Response.json({ release: "spherex_dr1", bands });
      const item = bands.find(({ file }) => url.endsWith(file));
      if (!item) throw new Error(`Unexpected request to ${url}`);
      return new Response(new Uint8Array([item.band]));
    });
    vi.stubGlobal("fetch", requests);
    const context = {
      createImageData: (width: number, height: number) => ({
        data: new Uint8ClampedArray(width * height * 4),
      }),
      putImageData: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      (kind) => (kind === "2d" ? context : null),
    );

    render(<App />);
    await screen.findByRole("img", { name: /Grayscale SPHEREx image/ });
    expect(screen.getByText("Surveys 1 and 2 combined", { selector: "dd" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Difference" }) as HTMLButtonElement).disabled)
      .toBe(true);
    const control = screen.getByRole<HTMLInputElement>("slider", { name: "Band" });
    expect(control.max).toBe("2");
    fireEvent.change(control, { target: { value: "2" } });
    await waitFor(() => expect(document.querySelector(".metadata")?.textContent).toContain("2.466 µm"));
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    const share = new URL(document.querySelector<HTMLInputElement>(".share-dialog input")!.value);
    expect(share.searchParams.get("sample")).toBe("m31");
    expect(share.searchParams.get("band")).toBe("53");
    expect(share.searchParams.get("source")).toBeNull();
    expect(requests.mock.calls.map(([url]) => url.split("/").at(-1))).toEqual([
      "manifest.json", bands[0].file, bands[1].file,
    ]);
  });
});
