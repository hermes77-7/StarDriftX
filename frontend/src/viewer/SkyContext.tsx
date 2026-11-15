import { useEffect, useEffectEvent, useImperativeHandle, useRef, useState } from "react";
import type { Ref } from "react";
import type { AladinViewer, HiPSLayer } from "aladin-lite";
import type { Messages } from "../messages";
import type { Coordinates } from "./search";

const surveys = [
  { name: "DSS2 Color", url: "https://stpubdata.s3.us-east-1.amazonaws.com/mast/skybackgrounds/DSSColor" },
  { name: "Pan-STARRS DR1", url: "https://stpubdata.s3.us-east-1.amazonaws.com/mast/skybackgrounds/PanSTARRS" },
  { name: "2MASS Color", url: "https://casda.csiro.au/hips/2MASS/Color" },
];
export type ContextTarget = Coordinates & { fov: number };

function readTarget(viewer: AladinViewer): ContextTarget {
  const [ra, dec] = viewer.getRaDec();
  return { ra, dec, fov: viewer.getFov()[0] };
}

function samePosition(a: Coordinates, b: Coordinates) {
  const separation = Math.abs(a.ra - b.ra) % 360;
  return Math.min(separation, 360 - separation) < 1e-6 && Math.abs(a.dec - b.dec) < 1e-6;
}

export function SkyContext({ active, t, target, camera }: {
  active: boolean;
  t: Messages;
  target: ContextTarget;
  camera: Ref<() => ContextTarget | undefined>;
}) {
  const [viewer, setViewer] = useState<AladinViewer>();
  const [surveyIndex, setSurveyIndex] = useState(0);
  const [retry, setRetry] = useState(0);
  const [initializationFailed, setInitializationFailed] = useState(false);
  const [tileStatus, setTileStatus] = useState<"loading" | "ready" | "error">("loading");
  const element = useRef<HTMLDivElement>(null);
  const layerRequest = useRef({ generation: 0, timeout: 0, debounce: 0 });
  useImperativeHandle(camera, () => () => viewer ? readTarget(viewer) : undefined, [viewer]);

  useEffect(() => {
    const request = layerRequest.current;
    return () => {
      request.generation++;
      window.clearTimeout(request.timeout);
      window.clearTimeout(request.debounce);
    };
  }, []);

  function trackLayer(layer: HiPSLayer, generation: number) {
    const request = layerRequest.current;
    function report(value: "ready" | "error") {
      if (request.generation !== generation) return;
      window.clearTimeout(request.timeout);
      setTileStatus(value);
    }
    // The Aladin HiPS factory accepts the name but ignores callbacks in its options.
    layer.successCallback = () => report("ready");
    layer.errorCallback = () => report("error");
    request.timeout = window.setTimeout(() => report("error"), 12000);
    return layer;
  }

  const initialize = useEffectEvent(async (signal: AbortSignal) => {
    const { default: Aladin } = await import("aladin-lite");
    const timeout = AbortSignal.timeout(12000);
    const ready = AbortSignal.any([signal, timeout]);
    await new Promise<void>((resolve, reject) => {
      const abort = () => reject(ready.reason);
      ready.addEventListener("abort", abort, { once: true });
      void Aladin.init.then(resolve, reject).finally(() => ready.removeEventListener("abort", abort));
    });
    if (signal.aborted || !element.current) return;
    const survey = surveys[surveyIndex];
    const generation = ++layerRequest.current.generation;
    const instance = Aladin.aladin(element.current, {
      target: [target.ra, target.dec].join(" "),
      fov: target.fov, cooFrame: "ICRSd", projection: "TAN",
      survey: trackLayer(Aladin.HiPS(survey.url, { name: survey.name }), generation),
      showReticle: true,
      showContextMenu: false, showSimbadPointerControl: false, showLayersControl: false,
    });
    instance.setFoVRange(0.00625, 1);
    return instance;
  });
  useEffect(() => {
    if (!active || viewer) return;
    const controller = new AbortController();
    void initialize(controller.signal).then(
      (instance) => {
        if (controller.signal.aborted || !instance) return;
        setInitializationFailed(false);
        setViewer(instance);
      },
      () => {
        if (!controller.signal.aborted) setInitializationFailed(true);
      },
    );
    return () => controller.abort();
  }, [active, viewer, retry]);

  function changeSurvey(index: number) {
    setTileStatus("loading");
    setInitializationFailed(false);
    setSurveyIndex(index);
    if (!viewer) {
      setRetry((value) => value + 1);
      return;
    }
    const request = layerRequest.current;
    const generation = ++request.generation;
    window.clearTimeout(request.timeout);
    window.clearTimeout(request.debounce);
    request.debounce = window.setTimeout(() => {
      const survey = surveys[index];
      void import("aladin-lite").then(({ default: Aladin }) => {
        if (request.generation === generation) {
          viewer.setBaseImageLayer(trackLayer(Aladin.HiPS(survey.url, { name: survey.name }), generation));
        }
      }).catch(() => {
        if (request.generation === generation) setTileStatus("error");
      });
    }, 150);
  }

  const { ra, dec, fov } = target;
  useEffect(() => {
    if (!viewer || !active) return;
    const current = readTarget(viewer);
    if (!samePosition(current, { ra, dec })) viewer.gotoRaDec(ra, dec);
    if (Math.abs(current.fov - fov) > 1e-6) viewer.setFoV(fov);
  }, [viewer, active, ra, dec, fov]);

  return (
    <section className="sky-context" aria-label={t.skyContext} hidden={!active}>
      <div className="image-stage">
        <div ref={element} className="aladin-context" />
      </div>
      <div className="image-toolbar">
        <label className="context-survey">
          <span>{t.backgroundSurvey}</span>
          <select value={surveyIndex} onChange={(event) => changeSurvey(Number(event.target.value))}>
            {surveys.map(({ name }, index) => <option key={name} value={index}>{name}</option>)}
          </select>
        </label>
        {(initializationFailed || tileStatus === "error") ? (
          <p role="status">
            {t.contextUnavailable}{" "}
            <button type="button" onClick={() => changeSurvey(surveyIndex)}>{t.retry}</button>
          </p>
        ) : tileStatus === "loading" ? (
          <p role="status">{t.contextLoading}</p>
        ) : <p>{t.contextScienceHint}</p>}
      </div>
    </section>
  );
}
