import {
  useCallback,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import type { Language, Messages } from "../messages";
import type { CatalogSource } from "../types/api";
import { CatalogOverlay } from "./CatalogOverlay";
import type { Cutout } from "./cutout";
import { drawPixels, subtractBackgroundGradient } from "./image";
import type { DisplaySettings } from "./image";
import {
  defaultNavigation,
  maxZoom,
  minZoom,
  moveView,
  skyAtPoint,
  skyScaleBar,
} from "./navigation";
import type { Navigation, Point } from "./navigation";
import { createWebGLRenderer } from "./webgl";

function gesture(points: Map<number, Point>) {
  const [first, second] = points.values();
  return second
    ? {
        center: { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 },
        span: Math.hypot(second.x - first.x, second.y - first.y),
      }
    : { center: first, span: 0 };
}

export function SkyImage({
  cutout,
  display,
  t,
  language,
  navigation,
  onNavigate,
  sharedContrast,
  label,
  sources,
  selectedSource,
  onSelectSource,
}: {
  cutout: Cutout;
  display: DisplaySettings;
  t: Messages;
  language: Language;
  navigation: Navigation;
  onNavigate: (view: Navigation) => void;
  sharedContrast?: Float32Array;
  label?: string;
  sources?: CatalogSource[];
  selectedSource?: CatalogSource;
  onSelectSource?: (source: CatalogSource) => void;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const frame = useRef<ImageData | null>(null);
  const gpu = useRef<ReturnType<typeof createWebGLRenderer>>(null);
  const uploadedPixels = useRef<Float32Array | null>(null);
  const [useCanvas2D, setUseCanvas2D] = useState(false);
  const activePointers = useRef(new Map<number, Point>());
  const currentView = useRef(navigation);
  const imageSize = useRef({ width: 0, height: 0 });
  const [displayWidth, setDisplayWidth] = useState(0);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [dragging, setDragging] = useState(false);
  const [drawingFailed, setDrawingFailed] = useState(false);
  const { zoom, pan } = navigation;
  const { subtractBackground, stretch, low, high, strength, colormap } =
    display;
  const { width, height } = cutout;
  const pixels = useMemo(
    () =>
      subtractBackground
        ? subtractBackgroundGradient(cutout.pixels, width, height)
        : cutout.pixels,
    [cutout.pixels, width, height, subtractBackground],
  );
  const sorted = useMemo(
    () => sharedContrast ?? pixels.filter(Number.isFinite).sort(),
    [pixels, sharedContrast],
  );

  useLayoutEffect(() => {
    const settings = {
      stretch,
      low,
      high,
      strength,
      colormap,
      subtractBackground,
    };
    const image = { width, height, pixels };
    const renderer = gpu.current;
    if (renderer) {
      if (uploadedPixels.current !== pixels) {
        if (!renderer.setImage(image)) {
          setUseCanvas2D(true);
          return;
        }
        uploadedPixels.current = pixels;
      }
      renderer.draw(sorted, settings);
      return;
    }
    const context = canvas.current?.getContext("2d");
    if (!context) return;
    if (
      !frame.current ||
      frame.current.width !== width ||
      frame.current.height !== height
    ) {
      frame.current = context.createImageData(width, height);
    }
    drawPixels(image, sorted, settings, frame.current.data);
    context.putImageData(frame.current, 0, 0);
  }, [
    width,
    height,
    pixels,
    sorted,
    stretch,
    low,
    high,
    strength,
    colormap,
    subtractBackground,
    useCanvas2D,
    displayWidth,
  ]);

  const checkCanvas = useCallback(
    (element: HTMLCanvasElement | null) => {
      if (!element) return;
      canvas.current = element;
      if (!useCanvas2D) {
        // A canvas that acquired WebGL cannot later acquire a 2D context.
        const renderer = createWebGLRenderer(element);
        if (!renderer) {
          setUseCanvas2D(true);
        } else {
          gpu.current = renderer;
        }
      } else if (!element.getContext("2d")) {
        setDrawingFailed(true);
      }

      const onContextLost = () => setUseCanvas2D(true);
      element.addEventListener("webglcontextlost", onContextLost);
      return () => {
        element.removeEventListener("webglcontextlost", onContextLost);
        gpu.current?.dispose();
        gpu.current = null;
        uploadedPixels.current = null;
        frame.current = null;
        canvas.current = null;
      };
    },
    [useCanvas2D],
  );

  useLayoutEffect(() => {
    currentView.current = navigation;
  }, [navigation]);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    function measure(width: number, height: number) {
      imageSize.current = { width, height };
      setDisplayWidth(width);
      setCursor(null);
    }
    measure(element.clientWidth, element.clientHeight);
    const observer = new ResizeObserver(([entry]) => {
      measure(entry.contentRect.width, entry.contentRect.height);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [drawingFailed, useCanvas2D]);

  function pointAt(clientX: number, clientY: number): Point | null {
    const rect = stage.current?.getBoundingClientRect();
    const { width, height } = imageSize.current;
    if (!rect || !width || !height) return null;
    return {
      x: (clientX - rect.left - rect.width / 2) / width,
      y: (clientY - rect.top - rect.height / 2) / height,
    };
  }

  function navigate(next: Navigation) {
    const previous = currentView.current;
    if (
      next.zoom === previous.zoom &&
      next.pan.x === previous.pan.x &&
      next.pan.y === previous.pan.y
    )
      return;
    currentView.current = next;
    onNavigate(next);
  }

  const wheelZoom = useEffectEvent((event: WheelEvent) => {
    if ((event.target as Element | null)?.closest("button")) return;
    const anchor = pointAt(event.clientX, event.clientY);
    if (!anchor || event.deltaY === 0) return;
    const view = currentView.current;
    const factor = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 320 : 1;
    const delta = Math.max(-160, Math.min(160, event.deltaY * factor));
    const next = moveView(view, view.zoom * Math.exp(-delta * 0.002), anchor);
    if (next.zoom === view.zoom && !event.ctrlKey) return;
    event.preventDefault();
    setCursor(anchor);
    navigate(next);
  });

  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => wheelZoom(event);
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, []);

  function startPan(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || (event.target as Element).closest("button"))
      return;
    const point = pointAt(event.clientX, event.clientY);
    const pointers = activePointers.current;
    if (!point || pointers.size >= 2) return;
    pointers.set(event.pointerId, point);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
    setCursor(event.pointerType === "touch" ? null : point);
  }

  function trackPointer(event: PointerEvent<HTMLDivElement>) {
    const point = pointAt(event.clientX, event.clientY);
    if (!point) return;
    const pointers = activePointers.current;
    if (pointers.has(event.pointerId)) {
      const before = gesture(pointers);
      pointers.set(event.pointerId, point);
      const after = gesture(pointers);
      const view = currentView.current;
      const targetZoom =
        before.span > 0 ? (view.zoom * after.span) / before.span : view.zoom;
      navigate(moveView(view, targetZoom, before.center, after.center));
    }
    if (event.pointerType !== "touch") setCursor(point);
  }

  function endPan(event: PointerEvent<HTMLDivElement>) {
    const pointers = activePointers.current;
    if (!pointers.delete(event.pointerId)) return;
    setDragging(pointers.size > 0);
    if (
      event.pointerType === "touch" ||
      event.type === "pointercancel" ||
      !event.currentTarget.matches(":hover")
    )
      setCursor(null);
  }

  function keyboardNavigate(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return;
    const steps: Record<string, Point> = {
      ArrowLeft: { x: -0.05, y: 0 },
      ArrowRight: { x: 0.05, y: 0 },
      ArrowUp: { x: 0, y: -0.05 },
      ArrowDown: { x: 0, y: 0.05 },
    };
    const view = currentView.current;
    if (event.key === "Home") navigate(defaultNavigation);
    else if (event.key === "+" || event.key === "=")
      navigate(moveView(view, view.zoom * 1.25));
    else if (event.key === "-") navigate(moveView(view, view.zoom / 1.25));
    else if (Object.hasOwn(steps, event.key))
      navigate(moveView(view, view.zoom, undefined, steps[event.key]));
    else return;
    event.preventDefault();
  }

  const coordinates = cursor && skyAtPoint(cutout, navigation, cursor);
  const renderWidth = useCanvas2D
    ? width
    : Math.min(
        2048,
        Math.max(width, Math.round(displayWidth * window.devicePixelRatio)),
      );
  const renderHeight = useCanvas2D
    ? height
    : Math.round((renderWidth * height) / width);
  const scale = skyScaleBar(
    width,
    displayWidth * zoom,
    cutout.wcs.degreesPerPixel[0],
  );
  const wavelengths = cutout.wavelengthRangeMicrons;
  const stack = cutout.epoch && cutout.band === undefined;
  const wavelength = stack && wavelengths
    ? wavelengths.map((value) => value.toLocaleString(language, { maximumFractionDigits: 3 })).join("–")
    : cutout.wavelengthMicrons.toLocaleString(language, { maximumFractionDigits: 3 });
  const formatCoordinate = (value: number) =>
    value.toLocaleString(language, {
      minimumFractionDigits: 5,
      maximumFractionDigits: 5,
    });

  return (
    <div
      className={`image-stage navigable-stage${dragging ? " is-dragging" : ""}`}
      ref={stage}
      role="region"
      tabIndex={0}
      aria-label={t.navigation}
      onPointerDown={startPan}
      onPointerMove={trackPointer}
      onPointerUp={endPan}
      onPointerCancel={endPan}
      onLostPointerCapture={endPan}
      onPointerLeave={() => setCursor(null)}
      onKeyDown={keyboardNavigate}
    >
      {drawingFailed ? (
        <p role="alert">{t.loadError}</p>
      ) : (
        <div className="image-frame">
          <canvas
          key={useCanvas2D ? "canvas2d" : "webgl2"}
          ref={checkCanvas}
          data-renderer={useCanvas2D ? "canvas2d" : "webgl2"}
          role="img"
          aria-label={
            display.colormap === "diverging" ? t.differenceCanvas :
              display.colormap === "grayscale" ? t.canvas : t.viridisCanvas
          }
          width={renderWidth}
          height={renderHeight}
          data-image-width={width}
          style={{
            transform: `translate(${pan.x * 100}%, ${pan.y * 100}%) scale(${zoom})`,
          }}
          />
          {displayWidth > 0 && sources && onSelectSource && (
            <CatalogOverlay
              sources={sources} cutout={cutout} navigation={navigation} imageWidth={displayWidth}
              selected={selectedSource} onSelect={onSelectSource}
            />
          )}
        </div>
      )}
      <span className="stage-label">
        {label ?? (cutout.epoch ? t.liveData : (cutout.release ?? t.mosaic))} ·{" "}
        {stack ? [t.detector, cutout.detector, t.stackShort].join(" ")
          : [t.band, cutout.band ?? cutout.channel].join(" ")} · {wavelength} µm
      </span>
      <div className="zoom-tools" role="group" aria-label={t.zoom}>
        <button
          aria-label={t.zoomIn}
          disabled={zoom >= maxZoom}
          onClick={() =>
            navigate(
              moveView(currentView.current, currentView.current.zoom * 1.25),
            )
          }
        >
          +
        </button>
        <button
          aria-label={t.zoomOut}
          disabled={zoom <= minZoom}
          onClick={() =>
            navigate(
              moveView(currentView.current, currentView.current.zoom / 1.25),
            )
          }
        >
          −
        </button>
        <button
          aria-label={t.reset}
          disabled={zoom === minZoom && pan.x === 0 && pan.y === 0}
          onClick={() => navigate(defaultNavigation)}
        >
          ⟲
        </button>
      </div>
      <div className="sky-guides">
        <div className="compass" aria-label={t.orientation} role="img">
          <span>{t.north} ↑</span>
          <span>← {t.east}</span>
        </div>
        {scale && (
          <div
            className="sky-scale"
            role="img"
            aria-label={`${t.angularScale}: ${scale.arcseconds >= 60 ? scale.arcseconds / 60 + "′" : scale.arcseconds + "″"}`}
          >
            <span>
              {scale.arcseconds >= 60
                ? scale.arcseconds / 60 + "′"
                : scale.arcseconds + "″"}
            </span>
            <span className="scale-line" style={{ width: scale.width }} />
          </div>
        )}
      </div>
      {coordinates && (
        <div
          className="cursor-coordinates"
          role="group"
          aria-label={t.cursorCoordinates}
        >
          <span>RA {formatCoordinate(coordinates.ra)}°</span>
          <span>Dec {formatCoordinate(coordinates.dec)}°</span>
        </div>
      )}
    </div>
  );
}
