import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import type { Language, Messages } from "../messages";
import {
  CutoutError,
  channelsPerDetector,
  cutoutUrl,
  defaultFovLimits,
  defaultPixelScale,
  detectorForBand,
  loadArchiveMeta,
  loadBandMetadata,
  loadCutout,
  loadM31Band,
  loadM31Bundle,
} from "./cutout";
import type { Cutout, LiveView, M31Bundle, SpectralSelection } from "./cutout";
import type { BandInfo, CatalogName, CatalogSource, Epoch, FovLimits, MetaResponse } from "../types/api";
import { BandControl } from "./BandControl";
import { SourceDetails } from "./CatalogOverlay";
import { catalogs, catalogLabels, sourceKey, useCatalog } from "./catalogLayer";
import { blinkContrast, comparisonIssue, differencePixels, isNumberedEpoch, numberedEpochs } from "./epochs";
import type { EpochPair, NumberedEpoch } from "./epochs";
import {
  clipStep,
  defaultDisplay,
  defaultStrength,
  strengthRange,
  subtractBackgroundGradient,
} from "./image";
import type { Colormap, DisplaySettings, Stretch } from "./image";
import { SkyImage } from "./SkyImage";
import { clampPan, defaultNavigation, maxZoom, pointAtSky, skyAtPoint } from "./navigation";
import { SkySearch } from "./SkySearch";
import { SkyContext } from "./SkyContext";
import type { ContextTarget } from "./SkyContext";
import { viewerHref } from "./viewState";
import type { readView } from "./viewState";
import { useEpochCutouts } from "./useEpochCutouts";

function FieldControl({
  fov,
  limits,
  pixelScale,
  t,
  language,
  change,
}: {
  fov: number;
  limits: FovLimits;
  pixelScale: number;
  t: Messages;
  language: Language;
  change: (value: number) => void;
}) {
  const [draft, setDraft] = useState(fov);
  const [numeric, setNumeric] = useState(String(fov));
  const commit = useEffectEvent(change);
  useEffect(() => {
    if (draft === fov) return;
    const timer = window.setTimeout(() => commit(draft), 280);
    return () => window.clearTimeout(timer);
  }, [draft, fov]);
  function apply(value: number) {
    const bounded = Number.isFinite(value)
      ? Math.max(limits.min_deg, Math.min(limits.max_deg, value))
      : fov;
    setDraft(bounded);
    setNumeric(String(bounded));
  }
  const side = Math.ceil((draft * 3600) / pixelScale);
  const size = ((side * side * 8) / 1024).toLocaleString(language, {
    maximumFractionDigits: 0,
  });
  return (
    <div className="fov-control">
      <div className="field-row">
        <label htmlFor="field-width">{t.field}</label>
        <div className="fov-value">
          <input
            id="field-width"
            type="number"
            step="any"
            min={limits.min_deg}
            max={limits.max_deg}
            value={numeric}
            onChange={(event) => setNumeric(event.target.value)}
            onBlur={(event) => apply(event.target.valueAsNumber)}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
          />
          <span>°</span>
        </div>
      </div>
      <input
        type="range"
        aria-label={t.fieldSlider}
        min={limits.min_deg}
        max={limits.max_deg}
        step="0.01"
        value={draft}
        onChange={(event) => apply(event.target.valueAsNumber)}
      />
      <div className="fov-limits">
        <span>{limits.min_deg}°</span>
        <span>{limits.max_deg}°</span>
      </div>
      <p className="fov-estimate">
        {t.estimate} {size} KiB
      </p>
    </div>
  );
}

function ViewDetails({
  cutout,
  t,
  language,
}: {
  cutout: Cutout;
  t: Messages;
  language: Language;
}) {
  const number = (value: number, digits: number) =>
    value.toLocaleString(language, {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    });
  const date = (mjd: number) =>
    new Intl.DateTimeFormat(language, {
      dateStyle: "medium",
      timeStyle: "medium",
      timeZone: "UTC",
    }).format(new Date((mjd - 40587) * 86400000));
  const wcs = cutout.wcs;

  return (
    <>
      <p className="muted">{t.observation}</p>
      <dl className="details-list">
        <div className="detail-wide">
          <dt>{t.center}</dt>
          <dd>
            {wcs.referenceDegrees
              .map((value) => `${number(value, 4)}°`)
              .join("  ")}
          </dd>
        </div>
        <div>
          <dt>{t.field}</dt>
          <dd>{number(cutout.width * Math.abs(wcs.degreesPerPixel[0]), 4)}°</dd>
        </div>
        <div>
          <dt>{t.scale}</dt>
          <dd>{number(Math.abs(wcs.degreesPerPixel[0]) * 3600, 2)}″</dd>
        </div>
        {(cutout.band ?? cutout.channel) !== undefined && (
          <div>
            <dt>{t.band}</dt>
            <dd>{cutout.band ?? cutout.channel}</dd>
          </div>
        )}
        <div>
          <dt>{t.wavelength}</dt>
          <dd>{cutout.channel === undefined && cutout.wavelengthRangeMicrons
            ? cutout.wavelengthRangeMicrons.map((value) => number(value, 3)).join("–")
            : number(cutout.wavelengthMicrons, 3)} µm</dd>
        </div>
        <div>
          <dt>{t.detector}</dt>
          <dd>{cutout.detector}</dd>
        </div>
        {(cutout.exposureCount ?? cutout.exposureMjd.length) > 0 && (
          <>
            <div>
              <dt>{t.exposures}</dt>
              <dd>{cutout.exposureCount ?? cutout.exposureMjd.length}</dd>
            </div>
            <div className="detail-wide">
              <dt>{t.time}</dt>
              <dd>
                {(cutout.exposureRange
                  ? [...new Set(cutout.exposureRange)]
                  : cutout.exposureMjd
                ).map((mjd) => (
                  <span key={mjd}>{date(mjd)}</span>
                ))}
              </dd>
            </div>
          </>
        )}
        {cutout.release && (
          <div>
            <dt>{t.release}</dt>
            <dd className="release-value" title={cutout.release}>
              {cutout.source === "dr1-cube" ? "DR1" : cutout.collections
                ?.map((value) => value.replace("spherex_", "").toUpperCase())
                .join(" + ") ?? cutout.release}
            </dd>
          </div>
        )}
        {(cutout.epoch || cutout.source === "m31-dr1") && (
          <div>
            <dt>{t.survey}</dt>
            <dd>{cutout.source === "m31-dr1" || cutout.source === "dr1-cube" ? t.m31Dr1Surveys
              : cutout.epoch === "all" ? t.allSurveys : cutout.epoch}</dd>
          </div>
        )}
      </dl>
      {cutout.coverageFraction !== undefined && (
        <div className="quality-card">
          <h3>{t.quality}</h3>
          <dl className="details-list">
            <div>
              <dt>{t.covered}</dt>
              <dd>{number(cutout.coverageFraction * 100, 1)}%</dd>
            </div>
            <div>
              <dt>{t.flagged}</dt>
              <dd>{number((cutout.flaggedFraction ?? 0) * 100, 1)}%</dd>
            </div>
          </dl>
          {cutout.masked && <p className="mask-status">{t.masked}</p>}
        </div>
      )}
    </>
  );
}

export function Viewer({
  t,
  language,
  view,
  onSearchChange,
}: {
  t: Messages;
  language: Language;
  view: ReturnType<typeof readView>;
  onSearchChange: (href: string) => void;
}) {
  const {
    available,
    invalid,
    display,
    zoom,
    pan,
    coordinates,
    sample,
    fov,
    live,
    difference,
    bundledBand,
    selection: spectral,
  } = view;
  const stack = spectral.mode === "stack";
  const liveUrl = live ? cutoutUrl(live) : null;
  const localM31 = available && sample === "m31";
  const sampleKey = available ? `${sample}:${bundledBand}` : null;
  const requestKey = liveUrl ?? sampleKey;
  const [context, setContext] = useState<"unopened" | "active" | "inactive">("unopened");
  const contextActive = context === "active";
  const readContextTarget = useRef<() => ContextTarget | undefined>(null);
  const [sampleResult, setSampleResult] = useState<{
    key: string;
    attempt: number;
    cutout?: Cutout;
    error?: string;
  } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [m31Bundle, setM31Bundle] = useState<M31Bundle | null>();
  const packagedM31 = localM31 && m31Bundle;
  const [meta, setMeta] = useState<MetaResponse | null>();
  const [bandMetadata, setBandMetadata] = useState<{ key: string; bands: BandInfo[] }>();
  const [positionError, setPositionError] = useState(false);
  const [overlays, setOverlays] = useState({ gaia: false, simbad: false, wise: false });
  const [catalogAttempt, setCatalogAttempt] = useState({ gaia: 0, simbad: 0, wise: 0 });
  const [magnitudes, setMagnitudes] = useState<Partial<Record<CatalogName, number>>>({ gaia: 18, wise: 17 });
  const [selection, setSelection] = useState<{ field: string; key: string }>();
  const positionInput = useRef<HTMLInputElement>(null);
  const [lastLoaded, setLastLoaded] = useState<{
    key: string;
    href: string;
  } | null>(null);
  const isLive = Boolean(liveUrl);
  const [pair, setPair] = useState<EpochPair>(["2", "1"]);
  const [blinkField, setBlinkField] = useState<string | null>(null);
  const [blinkSeconds, setBlinkSeconds] = useState(1);
  const [blinkPhase, setBlinkPhase] = useState(0);
  const field = live ? cutoutUrl({ ...live, epoch: "all" }) : null;
  const catalogField = [coordinates.ra, coordinates.dec, fov].join(",");
  const gaia = useCatalog("gaia", overlays.gaia && !contextActive, coordinates, fov, magnitudes.gaia, catalogAttempt.gaia);
  const simbad = useCatalog("simbad", overlays.simbad && !contextActive, coordinates, fov, magnitudes.simbad, catalogAttempt.simbad);
  const wise = useCatalog("wise", overlays.wise && !contextActive, coordinates, fov, magnitudes.wise, catalogAttempt.wise);
  const catalogState = { gaia, simbad, wise };
  const gaiaSources = gaia.state === "ready" ? gaia.data.sources : undefined;
  const simbadSources = simbad.state === "ready" ? simbad.data.sources : undefined;
  const wiseSources = wise.state === "ready" ? wise.data.sources : undefined;
  const sources = useMemo(
    () => [...gaiaSources ?? [], ...simbadSources ?? [], ...wiseSources ?? []],
    [gaiaSources, simbadSources, wiseSources],
  );
  const selectedSource = selection?.field === catalogField
    ? sources.find((source) => sourceKey(source) === selection.key)
    : undefined;
  function selectSource(source: CatalogSource) {
    setSelection(source === selectedSource ? undefined : { field: catalogField, key: sourceKey(source) });
  }
  function toggleCatalog(catalog: CatalogName) {
    setOverlays({ ...overlays, [catalog]: !overlays[catalog] });
  }
  const blinking = field !== null && blinkField === field && !difference;
  const activePair = difference ?? pair;
  const comparing = Boolean(difference || blinking);
  const currentEpoch = live?.epoch ?? "all";
  const collection = meta?.epochs?.find(({ id }) => id === currentEpoch)?.collection;
  const collectionEpoch = collection
    ? meta?.epochs.find((epoch) => epoch.collection === collection)?.id
    : currentEpoch;
  const calibrationEpoch = collectionEpoch && isNumberedEpoch(collectionEpoch)
    ? collectionEpoch : currentEpoch;
  const archiveRelease = meta?.data_release ?? "";
  const calibrationKey = isLive && !contextActive && meta !== undefined
    ? [archiveRelease, calibrationEpoch].join("/")
    : null;
  useEffect(() => {
    if (!calibrationKey) return;
    const controller = new AbortController();
    loadBandMetadata(calibrationEpoch, archiveRelease, controller.signal).then(
      (bands) => {
        if (!controller.signal.aborted) setBandMetadata({ key: calibrationKey, bands });
      },
      () => {
        if (!controller.signal.aborted) setBandMetadata({ key: calibrationKey, bands: [] });
      },
    );
    return () => controller.abort();
  }, [archiveRelease, calibrationEpoch, calibrationKey, attempt]);
  const primaryEpoch = comparing ? activePair[0] : currentEpoch;
  const secondaryEpoch = comparing ? activePair[1] : null;
  const supported = meta?.epochs
    ? numberedEpochs.filter((epoch) => meta.epochs.some(({ id }) => id === epoch))
    : numberedEpochs;
  const prefetch = currentEpoch !== "all" && meta !== undefined && live?.mode !== "stack"
    && fov < 0.5 && !secondaryEpoch
    ? activePair.find((epoch) => epoch !== primaryEpoch && supported.includes(epoch))
    : undefined;
  const loaded = useEpochCutouts(live, primaryEpoch, secondaryEpoch, attempt, !contextActive, prefetch);
  const primary = loaded[primaryEpoch];
  const secondary = secondaryEpoch ? loaded[secondaryEpoch] : undefined;
  const firstImage = primary && "cutout" in primary ? primary.cutout : undefined;
  const secondImage = secondary && "cutout" in secondary ? secondary.cutout : undefined;
  const issue = useMemo(() =>
    firstImage && secondImage ? comparisonIssue(firstImage, secondImage, Boolean(difference)) : null,
    [firstImage, secondImage, difference],
  );
  const isDifference = Boolean(difference);
  const differenceImage = useMemo(() =>
    isDifference && firstImage && secondImage && !issue
      ? { ...firstImage, pixels: differencePixels(firstImage.pixels, secondImage.pixels) }
      : undefined,
    [isDifference, firstImage, secondImage, issue],
  );
  const blinkImages = useMemo(() => {
    if (!blinking || !firstImage || !secondImage || issue) return;
    const prepare = (image: Cutout) => display.subtractBackground
      ? { ...image, pixels: subtractBackgroundGradient(image.pixels, image.width, image.height) }
      : image;
    const a = prepare(firstImage);
    const b = prepare(secondImage);
    return { a, b, contrast: blinkContrast(a.pixels, b.pixels) };
  }, [blinking, firstImage, secondImage, issue, display.subtractBackground]);
  const blinkReady = Boolean(blinkImages);
  useEffect(() => {
    if (!blinkReady || contextActive) return;
    const timer = window.setInterval(() => setBlinkPhase((phase) => 1 - phase), blinkSeconds * 1000);
    return () => window.clearInterval(timer);
  }, [blinkReady, contextActive, blinkSeconds, firstImage, secondImage]);

  const rememberView = useEffectEvent((key: string) => {
    setLastLoaded({ key, href: viewerHref(display, zoom, sample, pan, live, null, bundledBand) });
  });

  useEffect(() => {
    if (!isLive) return;
    const controller = new AbortController();
    loadArchiveMeta(controller.signal).then(
      (value) => {
        if (!controller.signal.aborted) setMeta(value);
      },
      () => {
        if (!controller.signal.aborted) setMeta(null);
      },
    );
    return () => controller.abort();
  }, [isLive, attempt]);

  useEffect(() => {
    if (!localM31) return;
    const controller = new AbortController();
    loadM31Bundle(controller.signal).then(
      (bundle) => { if (!controller.signal.aborted) setM31Bundle(bundle); },
      () => { if (!controller.signal.aborted) setM31Bundle(null); },
    );
    return () => controller.abort();
  }, [localM31, attempt]);

  useEffect(() => {
    if (!sampleKey || (localM31 && m31Bundle === undefined)) return;
    const controller = new AbortController();
    async function load() {
      if (localM31 && m31Bundle) return loadM31Band(m31Bundle, bundledBand, controller.signal);
      if (localM31 && bundledBand !== 4) {
        throw new CutoutError("NO_COVERAGE", "This M31 band is unavailable.");
      }
      return loadCutout(controller.signal, sample);
    }
    void load().then(
      (cutout) => {
        if (!controller.signal.aborted) {
          setSampleResult({ key: sampleKey, attempt, cutout });
          rememberView(sampleKey);
        }
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setSampleResult({
            key: sampleKey,
            attempt,
            error:
              error instanceof CutoutError ? error.code : "SERVICE_UNAVAILABLE",
          });
        }
      },
    );
    return () => controller.abort();
  }, [attempt, sample, sampleKey, localM31, m31Bundle, bundledBand]);

  const single = loaded[currentEpoch];
  useEffect(() => {
    if (!liveUrl || !single || !("cutout" in single) || comparing) return;
    const timer = window.setTimeout(() => rememberView(liveUrl), 0);
    return () => window.clearTimeout(timer);
  }, [liveUrl, single, comparing]);

  const current =
    sampleResult?.key === requestKey && sampleResult.attempt === attempt ? sampleResult : null;
  const singleImage = live ? (single && "cutout" in single ? single.cutout : undefined) : current?.cutout;
  const cutout = difference
    ? differenceImage
    : blinkImages ? (blinkPhase ? blinkImages.b : blinkImages.a) : singleImage;
  const cubeFallback = cutout?.source === "dr1-cube";
  const error = live ? (primary && "error" in primary ? primary.error : undefined) : current?.error;
  const comparisonError = comparing
    ? (primary && "error" in primary ? { epoch: activePair[0], code: primary.error } :
      secondary && "error" in secondary ? { epoch: activePair[1], code: secondary.error } : null)
    : null;
  const comparisonLabel = cubeFallback ? t.dr1Cube : difference
    ? `${t.epoch} ${activePair[0]} − ${t.epoch} ${activePair[1]}`
    : cutout?.epoch ? (cutout.epoch === "all" ? t.allSurveys : `${t.epoch} ${cutout.epoch}`) : undefined;
  const { ra, dec } = coordinates;
  const limits = meta?.fov_limits ?? defaultFovLimits;
  const pixelScale = meta?.pixel_scale_arcsec ?? defaultPixelScale;
  const formatCoordinate = (value: number) =>
    value.toLocaleString(language, {
      minimumFractionDigits: 4,
      maximumFractionDigits: 4,
    });
  const position = `${formatCoordinate(ra)}°  ${dec >= 0 ? "+" : ""}${formatCoordinate(dec)}°`;
  const updateDisplay = (changes: Partial<DisplaySettings>) =>
    onSearchChange(
      viewerHref({ ...display, ...changes }, zoom, sample, pan, live, difference, bundledBand),
    );
  const liveSelection = (changes: Partial<Pick<LiveView, "ra" | "dec" | "fov" | "epoch">> = {}): LiveView => ({
    ra, dec, fov, epoch: currentEpoch, ...spectral, ...changes,
  });
  function openLive(changes: Partial<Pick<LiveView, "ra" | "dec" | "fov" | "epoch">> = {}) {
    setBlinkField(null);
    onSearchChange(
      viewerHref(display, 1, sample, undefined, liveSelection(changes), difference),
    );
  }
  function selectSpectrum(next: SpectralSelection) {
    if (packagedM31 && next.mode !== "stack") {
      onSearchChange(viewerHref(display, zoom, sample, pan, null, null, next.band));
      return;
    }
    if (live && (next.mode === "stack"
      ? stack && next.detector === spectral.detector
      : !stack && next.band === spectral.band)) return;
    setBlinkField(null);
    onSearchChange(viewerHref(
      display, zoom, sample, pan, { ra, dec, fov, epoch: currentEpoch, ...next },
      next.mode === "stack" ? null : difference,
    ));
  }
  function selectEpoch(epoch: Epoch) {
    setBlinkField(null);
    if (epoch === "all" && available) return;
    onSearchChange(viewerHref(display, zoom, sample, pan, liveSelection({ epoch })));
  }
  function choosePair(next: EpochPair) {
    setPair(next);
    setBlinkPhase(0);
    if (difference) {
      onSearchChange(viewerHref(display, zoom, sample, pan, liveSelection(), next));
    }
  }
  function switchComparison(next: EpochPair | null) {
    setBlinkField(null);
    setPair(activePair);
    onSearchChange(viewerHref(display, zoom, sample, pan, liveSelection(), next));
  }
  function showScience(differenceMode: boolean) {
    const nextPair = differenceMode ? activePair : null;
    const changedMode = differenceMode !== Boolean(difference);
    if (!contextActive) {
      if (changedMode) switchComparison(nextPair);
      return;
    }
    const target = readContextTarget.current?.();
    setContext("inactive");
    if (!target || sameCamera(target) || !Number.isFinite(target.ra + target.dec + target.fov) || target.fov <= 0) {
      if (changedMode) switchComparison(nextPair);
      return;
    }
    setBlinkField(null);
    setPair(activePair);
    onSearchChange(scienceHref(target, nextPair));
  }
  const errorText =
    error === "NO_COVERAGE"
      ? localM31 ? t.m31Dr1NoBand : t.noCoverageHelp
      : error === "UPSTREAM_TIMEOUT"
        ? t.timeout
        : error === "RATE_LIMIT_EXCEEDED"
          ? t.rateLimit
          : error === "FOV_OUT_OF_RANGE"
            ? t.fieldInvalid
            : t.loadError;
  const center =
    cutout && (zoom > 1 || pan.x !== 0 || pan.y !== 0)
      ? skyAtPoint(cutout, { zoom, pan }, { x: 0, y: 0 })
      : null;
  const imageFov = cutout ? cutout.width * Math.abs(cutout.wcs.degreesPerPixel[0]) : fov;
  const contextTarget = { ...(center ?? coordinates), fov: imageFov / zoom };
  function sameCamera(target: ContextTarget) {
    const separation = Math.abs(target.ra - contextTarget.ra) % 360;
    return Math.min(separation, 360 - separation) < 1e-6 &&
      Math.abs(target.dec - contextTarget.dec) < 1e-6 &&
      Math.abs(target.fov - contextTarget.fov) < 1e-6;
  }
  function scienceHref(target: ContextTarget, nextPair: EpochPair | null) {
    const nextZoom = imageFov / target.fov;
    const point = cutout && pointAtSky(cutout, defaultNavigation, target);
    if (point && nextZoom >= 1 && nextZoom <= maxZoom) {
      const pan = clampPan({ x: -point.x * nextZoom, y: -point.y * nextZoom }, nextZoom);
      return viewerHref(display, nextZoom, sample, pan, nextPair ? liveSelection() : live,
        nextPair, bundledBand);
    }
    const nextFov = Math.min(limits.max_deg, Math.max(limits.min_deg, Number(target.fov.toFixed(6))));
    return viewerHref(display, Math.min(maxZoom, Math.max(1, nextFov / target.fov)), sample,
      undefined, liveSelection({
      ra: Number(target.ra.toFixed(7)) % 360, dec: Number(target.dec.toFixed(7)),
      fov: nextFov,
    }), nextPair);
  }
  const comparisonMessage = issue
    ? t[issue]
    : comparisonError
      ? comparisonError.code === "NO_COVERAGE"
        ? t.epochUncovered
        : t.epochFailed
      : comparing && (!firstImage || !secondImage)
        ? t.loadingPair
        : null;
  const comparisonAlert = Boolean(issue || comparisonError);
  const normalized = firstImage?.photometricNormalized && secondImage?.photometricNormalized;
  const selectionLabel = (epoch: Epoch) => epoch === "all" ? t.allSurveys : [t.epoch, epoch].join(" ");
  const observationDate = new Intl.DateTimeFormat(language, { dateStyle: "medium", timeZone: "UTC" });
  function formatDateRange(image: Cutout | undefined) {
    if (!image?.exposureRange) return undefined;
    const [start, end] = image.exposureRange.map((mjd) => new Date((mjd - 40587) * 86400000));
    return observationDate.formatRange(start, end);
  }
  const canvasDisplay: DisplaySettings = difference
    ? { ...display, colormap: "diverging", subtractBackground: false }
    : blinkImages ? { ...display, subtractBackground: false } : display;

  return (
    <>
      <main className="workspace">
        <aside className="controls panel" aria-label={t.controls}>
          <h2 className="eyebrow">{t.controls}</h2>
          <section>
            <h3>{t.position}</h3>
            <SkySearch t={t} onPosition={(position) => {
              if (liveUrl === cutoutUrl(liveSelection(position)) &&
                  (error || comparisonError || cubeFallback)) {
                setAttempt((value) => value + 1);
              }
              openLive(position);
            }} />
            <nav className="sample-links" aria-label={t.samplePicker}>
              <a
                className="button"
                href={viewerHref(display)}
                aria-current={
                  available && sample === "scope" ? "page" : undefined
                }
              >
                {t.scopeField}
              </a>
              <a
                className="button"
                href={viewerHref(display, 1, "m31")}
                aria-current={
                  available && sample === "m31" ? "page" : undefined
                }
              >
                {t.m31Field}
              </a>
            </nav>
            <form
              className="position-form"
              key={`${ra},${dec}`}
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                const nextRa = Number(form.get("ra"));
                const nextDec = Number(form.get("dec"));
                if (
                  !form.get("ra") ||
                  !form.get("dec") ||
                  nextRa < 0 ||
                  nextRa >= 360 ||
                  nextDec < -90 ||
                  nextDec > 90 ||
                  !Number.isFinite(nextRa + nextDec)
                ) {
                  setPositionError(true);
                  return;
                }
                setPositionError(false);
                openLive({ ra: nextRa, dec: nextDec });
              }}
            >
              <div className="coordinate-fields">
                <label className="field">
                  <span>{t.raDegrees}</span>
                  <input
                    ref={positionInput}
                    name="ra"
                    type="number"
                    step="any"
                    defaultValue={ra}
                    required
                  />
                </label>
                <label className="field">
                  <span>{t.decDegrees}</span>
                  <input
                    name="dec"
                    type="number"
                    step="any"
                    defaultValue={dec}
                    required
                  />
                </label>
              </div>
              <button className="load-field" type="submit">
                {t.loadField}
              </button>
              {positionError && (
                <p role="alert" className="form-error">
                  {t.coordinatesInvalid}
                </p>
              )}
            </form>
            <FieldControl
              key={fov}
              fov={fov}
              limits={limits}
              pixelScale={pixelScale}
              t={t}
              language={language}
              change={(value) => openLive({ fov: value })}
            />
          </section>
          {requestKey && (
            <section>
              <h3>{t.display}</h3>
              <label className="field">
                <span>{t.stretch}</span>
                <select
                  value={display.stretch}
                  onChange={(event) => {
                    const stretch = event.target.value as Stretch;
                    updateDisplay({
                      stretch,
                      strength: defaultStrength[stretch],
                    });
                  }}
                >
                  <option value="linear">{t.linear}</option>
                  <option value="log">{t.log}</option>
                  <option value="asinh">{t.asinh}</option>
                </select>
              </label>
              <fieldset className="clip">
                <legend>{t.clip}</legend>
                <label>
                  <span>{t.black}</span>
                  <output>{display.low}%</output>
                  <input
                    type="range"
                    aria-label={t.black}
                    min="0"
                    max={
                      Math.floor((display.high - clipStep) / clipStep) *
                      clipStep
                    }
                    step={clipStep}
                    value={display.low}
                    onChange={(event) =>
                      updateDisplay({ low: event.target.valueAsNumber })
                    }
                  />
                </label>
                <label>
                  <span>{t.white}</span>
                  <output>{display.high}%</output>
                  <input
                    type="range"
                    aria-label={t.white}
                    min={
                      Math.ceil((display.low + clipStep) / clipStep) * clipStep
                    }
                    max="100"
                    step={clipStep}
                    value={display.high}
                    onChange={(event) =>
                      updateDisplay({ high: event.target.valueAsNumber })
                    }
                  />
                </label>
              </fieldset>
              {display.stretch !== "linear" && (
                <div className="clip">
                  <label>
                    <span>{t.strength}</span>
                    <output>{display.strength}</output>
                    <input
                      type="range"
                      aria-label={t.strength}
                      min={strengthRange.min}
                      max={strengthRange.max}
                      value={display.strength}
                      onChange={(event) =>
                        updateDisplay({ strength: event.target.valueAsNumber })
                      }
                    />
                  </label>
                </div>
              )}
              <label className="field display-option">
                <span>{t.colormap}</span>
                <select
                  value={canvasDisplay.colormap}
                  disabled={Boolean(difference)}
                  onChange={(event) =>
                    updateDisplay({ colormap: event.target.value as Colormap })
                  }
                >
                  <option value="grayscale">{t.grayscale}</option>
                  <option value="viridis">Viridis</option>
                  {difference && <option value="diverging">{t.amberViolet}</option>}
                </select>
              </label>
              <label className="background-option">
                <input
                  type="checkbox"
                  checked={!difference && display.subtractBackground}
                  disabled={Boolean(difference)}
                  onChange={(event) =>
                    updateDisplay({ subtractBackground: event.target.checked })
                  }
                />
                {t.subtractBackground}
              </label>
              <button
                className="reset-display"
                onClick={() => updateDisplay(defaultDisplay)}
              >
                {t.resetDisplay}
              </button>
            </section>
          )}
          {requestKey && (
            <section className="catalog-controls">
              <h3>{t.catalogOverlays}</h3>
              {catalogs.map((catalog) => {
                const status = catalogState[catalog];
                const magnitudeLabel = { gaia: t.gaiaLimit, wise: t.wiseLimit, simbad: t.simbadLimit }[catalog];
                return (
                  <div key={catalog}>
                    <label className="background-option">
                      <input type="checkbox" checked={overlays[catalog]} onChange={() => toggleCatalog(catalog)} />
                      <span className={"catalog-legend marker-" + catalog} aria-hidden="true" />
                      {catalogLabels[catalog]}
                    </label>
                    {status.state === "loading" && <p role="status">{t.catalogLoading}</p>}
                    {status.state === "ready" && status.data.truncated && <p>{t.catalogTruncated}</p>}
                    {status.state === "error" && (
                      <p role="status">{t.catalogUnavailable}{" "}
                        <button type="button" onClick={() => setCatalogAttempt((attempt) => ({
                          ...attempt, [catalog]: attempt[catalog] + 1,
                        }))}>{t.retry}</button>
                      </p>
                    )}
                    {overlays[catalog] && (
                      <label className="field catalog-magnitude">
                        <span>{magnitudeLabel}</span>
                        <select value={magnitudes[catalog] ?? ""} onChange={(event) => {
                          const value = event.target.value;
                          setMagnitudes({ ...magnitudes, [catalog]: value ? Number(value) : undefined });
                        }}>
                          <option value="">{t.allMagnitudes}</option>
                          {[10, 12, 14, 16, 17, 18, 20, 21].map((magnitude) => (
                            <option key={magnitude}>{magnitude}</option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                );
              })}
            </section>
          )}
        </aside>
        <section className="explorer" aria-labelledby="viewer-title">
          <div className="explorer-heading">
            <div>
              <h1 id="viewer-title">
                {contextActive ? t.skyContextTab : comparing ? t.epochComparison : stack ? t.detectorStack : t.explorer}
              </h1>
              <p>
                {position}
                {available &&
                  ` / ${packagedM31 ? t.m31Dr1 : sample === "m31" ? t.m31Field : t.scopeField}`}
              </p>
            </div>
          </div>
          <div className="image-heading">
            <div className="view-modes" role="group" aria-label={t.viewMode}>
              <button
                type="button" aria-pressed={!contextActive && !difference}
                onClick={() => showScience(false)}
              >{t.image}</button>
              <button
                type="button" aria-pressed={!contextActive && Boolean(difference)}
                disabled={!requestKey || supported.length < 2 || stack || Boolean(packagedM31)}
                title={packagedM31 ? t.m31Dr1Surveys : stack ? t.differenceNeedsBand : undefined}
                onClick={() => showScience(true)}
              >{t.difference}</button>
              <button type="button" aria-pressed={contextActive} onClick={() => setContext("active")}>
                {t.skyContextTab}
              </button>
            </div>
            {!contextActive && requestKey && !packagedM31 && (
              <div className="spectral-modes" role="group" aria-label={t.spectralMode}>
                <button type="button" aria-pressed={!stack}
                  onClick={() => {
                    if (stack) selectSpectrum({ band: (spectral.detector - 1) * channelsPerDetector + 4 });
                  }}>{t.singleBand}</button>
                <button type="button" aria-pressed={stack}
                  onClick={() => {
                    if (!stack) selectSpectrum({ mode: "stack", detector: detectorForBand(spectral.band) });
                  }}>{t.detectorStack}</button>
              </div>
            )}
          </div>
          {invalid && (
            <p className="notice" role="status">
              {t.invalid}
            </p>
          )}
          {context !== "unopened" && (
            <SkyContext
              active={contextActive}
              t={t}
              target={contextTarget}
              camera={readContextTarget}
            />
          )}
          <div hidden={contextActive}>
            {cutout ? (
              <SkyImage
                cutout={cutout}
                display={canvasDisplay}
                sharedContrast={blinkImages?.contrast}
                label={comparisonLabel}
                sources={sources}
                selectedSource={selectedSource}
                onSelectSource={selectSource}
                t={t}
                language={language}
                navigation={{ zoom, pan }}
                onNavigate={(next) =>
                  onSearchChange(
                    viewerHref(display, next.zoom, sample, next.pan, live, difference, bundledBand),
                  )
                }
              />
            ) : (
              <div className="image-stage empty-stage">
                {difference ? (
                  <>
                    {!comparisonAlert && <span className="load-indicator" aria-hidden="true" />}
                    <p role={comparisonAlert ? "alert" : "status"}>
                      {comparisonError && selectionLabel(comparisonError.epoch) + " · "}
                      {comparisonMessage}
                    </p>
                    {comparisonError && comparisonError.code !== "NO_COVERAGE" && (
                      <button onClick={() => setAttempt((value) => value + 1)}>{t.retry}</button>
                    )}
                  </>
                ) : !requestKey || error ? (
                  <>
                    <span className="empty-icon" aria-hidden="true">
                      ⌖
                    </span>
                    <h2>
                      {error === "NO_COVERAGE" ? t.noCoverage : t.unavailable}
                    </h2>
                    <p role="alert">
                      {error ? errorText : t.unavailableSelection}
                    </p>
                    <div className="recovery-links">
                      {error && error !== "NO_COVERAGE" && (
                        <button
                          className="primary"
                          onClick={() => setAttempt((value) => value + 1)}
                        >
                          {t.retry}
                        </button>
                      )}
                      {live &&
                        (error === "UPSTREAM_TIMEOUT" ||
                          error === "SERVICE_UNAVAILABLE") &&
                        fov > limits.min_deg && (
                          <button
                            onClick={() =>
                              openLive({
                                fov: Math.max(
                                  limits.min_deg,
                                  Number((fov / 2).toFixed(6)),
                                ),
                              })
                            }
                          >
                            {t.narrowField}
                          </button>
                        )}
                      {error === "NO_COVERAGE" && live && (
                        <button
                          className="primary"
                          onClick={() =>
                            openLive({
                              fov: Math.min(limits.max_deg, fov * 2),
                              epoch: "all",
                            })
                          }
                          disabled={
                            fov === limits.max_deg && live.epoch === "all"
                          }
                        >
                          {live.epoch === "all" ? t.widenField : t.allSurveys}
                        </button>
                      )}
                      <button onClick={() => positionInput.current?.focus()}>
                        {t.editPosition}
                      </button>
                      {lastLoaded && lastLoaded.key !== requestKey && (
                        <button onClick={() => onSearchChange(lastLoaded.href)}>
                          {t.previousField}
                        </button>
                      )}
                    </div>
                    <a
                      className="recovery-sample"
                      href={viewerHref(display, 1, "m31")}
                    >
                      {t.openSample}
                    </a>
                  </>
                ) : (
                  <>
                    <span className="load-indicator" aria-hidden="true" />
                    <p role="status">{t.loading}</p>
                    {isLive && <p>{t.archiveLoading}</p>}
                    {lastLoaded && lastLoaded.key !== requestKey && (
                      <button onClick={() => onSearchChange(lastLoaded.href)}>
                        {t.previousField}
                      </button>
                    )}
                  </>
                )}
              </div>
            )}
            {cubeFallback && (
              <p className="notice partial-notice" role="status">
                {t.dr1CubeFallback}{" "}
                <button type="button" onClick={() => setAttempt((value) => value + 1)}>
                  {t.retryLiveCutout}
                </button>
              </p>
            )}
            {!!(cutout?.warnings?.length || (comparing &&
              (firstImage?.warnings?.length || secondImage?.warnings?.length))) && (
              <p className="notice partial-notice" role="status">
                {t.partial}
              </p>
            )}
            {differenceImage && (
              <div className="difference-legend" aria-label={t.differenceLegend}>
                <div className="difference-ramp" aria-hidden="true" />
                <div>
                  <span>− {selectionLabel(activePair[1])}</span>
                  <span>0</span>
                  <span>+ {selectionLabel(activePair[0])}</span>
                </div>
                <p>{t.differenceLegendHelp}</p>
              </div>
            )}
            {packagedM31 ? (
              <section className="epoch-strip" aria-label={t.observationTime}>
                <h2>{t.observationTime}</h2>
                <div className="epoch-buttons">
                  <span>{t.m31Dr1Surveys}</span>
                  <button type="button" onClick={() => openLive()}>{t.m31Dr1Live}</button>
                </div>
              </section>
            ) : requestKey && (
              <section className="epoch-strip" aria-label={t.observationTime}>
                <h2>{t.observationTime}</h2>
                <div className="epoch-buttons" role="group" aria-label={t.epoch}>
                  {(["all", ...supported] as const).map((epoch) => (
                    <button
                      key={epoch} type="button"
                      aria-pressed={!comparing && (live ? currentEpoch === epoch : epoch === "all")}
                      onClick={() => selectEpoch(epoch)}
                  title={loaded[epoch] && "error" in loaded[epoch]
                        ? loaded[epoch].error === "NO_COVERAGE" ? t.epochUncovered : t.epochFailed
                        : undefined}
                    >{epoch === "all" && available ? t.sample : selectionLabel(epoch)}</button>
                  ))}
                </div>
                <div className="blink-controls">
                  <button
                    type="button" disabled={supported.length < 2}
                    onClick={() => {
                      if (blinking) {
                        setBlinkField(null);
                        return;
                      }
                      const selection = liveSelection();
                      setPair(activePair);
                      setBlinkPhase(0);
                      setBlinkField(cutoutUrl({ ...selection, epoch: "all" }));
                      if (!live || difference) {
                        onSearchChange(viewerHref(display, zoom, sample, pan, selection));
                      }
                    }}
                  >{blinking ? t.stopBlink : t.startBlink}</button>
                  <label className="blink-rate">
                    <span>{t.blinkRate}</span>
                    <select value={blinkSeconds} onChange={(event) =>
                      setBlinkSeconds(Number(event.target.value))}
                    >
                      {[0.5, 1, 1.5, 2, 3].map((seconds) => (
                        <option key={seconds} value={seconds}>{seconds.toLocaleString(language)} s</option>
                      ))}
                    </select>
                  </label>
                </div>
                {blinking && comparisonMessage && (
                  <p role={comparisonAlert ? "alert" : "status"}>
                    {comparisonError && selectionLabel(comparisonError.epoch) + " · "}
                    {comparisonMessage}
                    {comparisonError && comparisonError.code !== "NO_COVERAGE" && (
                      <button onClick={() => setAttempt((value) => value + 1)}>{t.retry}</button>
                    )}
                  </p>
                )}
              </section>
            )}
            {requestKey && (
              <BandControl
                key={stack ? "stack" : spectral.band}
                selection={spectral} cutout={cutout} language={language} t={t}
                bands={packagedM31 ? packagedM31.bands
                  : bandMetadata?.key === calibrationKey ? bandMetadata?.bands : undefined}
                availableBands={packagedM31 ? packagedM31.bands : undefined}
                onChange={selectSpectrum}
              />
            )}
            {cutout && (
              <div className="image-toolbar">
                <span>
                  {cutout.width} × {cutout.height} px <i aria-hidden="true">·</i>{" "}
                  {cutout.unit}
                </span>
                {center && (
                  <button
                    onClick={() =>
                      openLive({
                        ra: Number(center.ra.toFixed(7)),
                        dec: Number(center.dec.toFixed(7)),
                        fov: Math.max(
                          limits.min_deg,
                          Number((fov / zoom).toFixed(4)),
                        ),
                      })
                    }
                  >
                    {t.loadZoomedField}
                  </button>
                )}
              </div>
            )}
          </div>
        </section>
        <aside className="metadata panel" aria-labelledby="details-title">
          <h2 id="details-title">{comparing ? t.comparisonSettings : t.details}</h2>
          {selectedSource && <SourceDetails source={selectedSource} language={language} t={t} clear={() => setSelection(undefined)} />}
          {comparing && (
            <div className="comparison-settings">
              {activePair.map((epoch, index) => (
                <div key={index}>
                  <label className="field">
                    <span>{index === 0 ? t.epochA : t.epochB}</span>
                    <select value={epoch} onChange={(event) => {
                      const value = event.target.value as NumberedEpoch;
                      choosePair(index === 0 ? [value, activePair[1]] : [activePair[0], value]);
                    }}>
                      {numberedEpochs.filter((option) => supported.includes(option) || option === epoch)
                        .map((option) => (
                          <option key={option} value={option} disabled={option === activePair[1 - index]}>
                            {selectionLabel(option)}
                          </option>
                        ))}
                    </select>
                  </label>
                  <p className="epoch-dates">{formatDateRange(index === 0 ? firstImage : secondImage)}</p>
                </div>
              ))}
              <button onClick={() => choosePair([activePair[1], activePair[0]])}>{t.swapEpochs}</button>
              {differenceImage && (
                <>
                  <p className="comparison-verified">{t.compatibleEpochs}</p>
                  <div className={normalized ? "comparison-verified" : "normalization-note"}>
                    <strong>{normalized ? t.normalized : t.unnormalized}</strong>
                    {!normalized && <p>{t.normalizationHelp}</p>}
                  </div>
                </>
              )}
            </div>
          )}
          {cutout && !difference ? (
            <ViewDetails cutout={cutout} t={t} language={language} />
          ) : !comparing && (
            <div className="metadata-empty">
              <p>
                {error === "NO_COVERAGE" ? t.noCoverage : t.requestedPosition}
              </p>
              <strong>{position}</strong>
            </div>
          )}
        </aside>
      </main>
      <footer className="workspace-footer">
        <span>{cubeFallback ? t.dr1Cube : isLive ? t.liveData : packagedM31 ? t.m31Dr1 : t.sample}</span>
        <span>{t.archive}</span>
        <span>{t.orientation}</span>
      </footer>
    </>
  );
}
