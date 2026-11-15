import { useEffect, useEffectEvent, useState } from "react";
import type { Language, Messages } from "../messages";
import type { BandInfo } from "../types/api";
import { bandCount, channelsPerDetector, detectorCount, detectorForBand } from "./cutout";
import type { Cutout, SpectralSelection } from "./cutout";

export function BandControl({ selection, cutout, bands, availableBands, language, t, onChange }: {
  selection: SpectralSelection;
  cutout?: Cutout;
  bands?: BandInfo[];
  availableBands?: BandInfo[];
  language: Language;
  t: Messages;
  onChange: (selection: SpectralSelection) => void;
}) {
  const stack = selection.mode === "stack";
  const band = stack ? null : selection.band;
  const completeBands = bands?.length === bandCount ? bands : undefined;
  const [draft, setDraft] = useState(band);
  const shownBand = draft ?? band;
  const sliderPosition = availableBands?.findIndex((item) => item.band === shownBand);
  const selectedBand = bands?.find((item) => item.band === shownBand);
  const detector = stack ? selection.detector
    : selectedBand?.detector ?? detectorForBand(shownBand ?? selection.band);
  const detectorIds = [...new Set(bands?.map((item) => item.detector))];
  const detectors = availableBands || detectorIds.length === detectorCount ? detectorIds
    : Array.from({ length: detectorCount }, (_, index) => index + 1);
  const commit = useEffectEvent(onChange);

  const pending = band !== null && draft !== null && draft !== band;
  useEffect(() => {
    if (!pending || draft === null) return;
    const timer = window.setTimeout(() => commit({ band: draft }), 300);
    return () => window.clearTimeout(timer);
  }, [pending, draft]);

  const displayedCutout = !pending && cutout?.detector === detector &&
    (stack ? cutout.band === undefined : cutout.band === shownBand || !cutout.epoch)
    ? cutout : undefined;
  const detectorBands = bands?.filter((item) => item.detector === detector);
  const detectorRange = detectorBands?.length
    ? [Math.min(...detectorBands.map((item) => item.wavelength_um)),
      Math.max(...detectorBands.map((item) => item.wavelength_um))]
    : undefined;
  const format = new Intl.NumberFormat(language, { maximumFractionDigits: 3 });
  const nominal = displayedCutout?.wavelengthMicrons ?? selectedBand?.wavelength_um;
  const range = displayedCutout?.wavelengthRangeMicrons ?? detectorRange;

  function changeDetector(value: number) {
    if (stack) {
      onChange({ mode: "stack", detector: value });
      return;
    }
    const channel = selectedBand?.channel ?? ((shownBand ?? 1) - 1) % channelsPerDetector + 1;
    const target = bands?.find((item) => item.detector === value && item.channel === channel) ??
      availableBands?.find((item) => item.detector === value);
    onChange({ band: target?.band ?? (value - 1) * channelsPerDetector + channel });
  }

  return (
    <section className="spectral-controls" aria-label={t.wavelength}>
      <div className="spectral-description">
        <h2>{t.wavelength}</h2>
        <strong>{stack ? [t.detector, detector, t.stackShort].join(" ") : [t.band, shownBand, t.ofBands, bandCount].join(" ")}</strong>
        <span>
          {!stack && nominal !== undefined && format.format(nominal) + " µm · "}
          {stack && range && range.map((value) => format.format(value)).join("–") + " µm · "}
          {t.detector} {detector}
        </span>
        {stack && <small>{t.stackDescription}</small>}
      </div>
      <div className="spectral-inputs">
        <div className="detectors" role="group" aria-label={t.detector}>
          {detectors.map((value) => (
            <button
              key={value} type="button" aria-pressed={detector === value}
              aria-label={t.detector + " " + value}
              onClick={() => changeDetector(value)}
            >D{value}</button>
          ))}
        </div>
        {shownBand !== null && (
          <label className="band-slider">
            <span>{t.band}</span>
            <input type="range" min={availableBands ? 1 : completeBands?.[0].band ?? 1}
              max={availableBands?.length ?? completeBands?.at(-1)?.band ?? bandCount}
              step="1"
              value={sliderPosition === undefined ? shownBand : Math.max(1, sliderPosition + 1)}
              aria-valuetext={availableBands ? `${t.band} ${shownBand}` : undefined}
              onChange={(event) => {
                const index = event.target.valueAsNumber;
                setDraft(availableBands ? availableBands[index - 1]?.band ?? band : index);
              }}
            />
          </label>
        )}
        {!stack && (
          <div className="spectral-scale">
            <span>{bands?.length ? format.format(bands[0].wavelength_um) + " µm" : "1"}</span>
            <span>{bands?.length ? format.format(bands[bands.length - 1].wavelength_um) + " µm" : bandCount}</span>
          </div>
        )}
        {bands?.length === 0 && <p className="spectral-status">{t.nominalUnavailable}</p>}
      </div>
    </section>
  );
}
