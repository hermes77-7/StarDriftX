import { useId, useMemo } from "react";
import type { Messages } from "../messages";
import type { CatalogSource } from "../types/api";
import type { Cutout } from "./cutout";
import { pointAtSky } from "./navigation";
import type { Navigation } from "./navigation";
import { catalogLabels, sourceKey } from "./catalogLayer";

export function CatalogOverlay({ sources, cutout, navigation, imageWidth, selected, onSelect }: {
  sources: CatalogSource[];
  cutout: Cutout;
  navigation: Navigation;
  imageWidth: number;
  selected?: CatalogSource;
  onSelect: (source: CatalogSource) => void;
}) {
  const { width, height, wcs } = cutout;
  const { zoom, pan } = navigation;
  const tooltipId = useId();
  const points = useMemo(() => {
    const labels: { x: number; y: number }[] = [];
    const cells = new Set<string>();
    const imageHeight = imageWidth * height / width;
    const ordered = [...sources].sort((a, b) => {
      if (a === selected) return -1;
      if (b === selected) return 1;
      return Number(b.catalog === "simbad") - Number(a.catalog === "simbad") ||
        (a.magnitude ?? Infinity) - (b.magnitude ?? Infinity);
    });
    return ordered.flatMap((source) => {
      const point = pointAtSky({ width, height, wcs }, { zoom, pan }, source);
      if (!point) return [];
      const x = point.x * imageWidth, y = point.y * imageHeight;
      const cell = [Math.floor(x / 8), Math.floor(y / 8)].join(",");
      if (cells.has(cell) && source !== selected) return [];
      cells.add(cell);
      const eligible = source === selected || source.catalog === "simbad" || zoom >= 2;
      const showLabel = source === selected || (
        eligible && labels.length < 20 &&
        !labels.some((label) => Math.abs(x - label.x) < 154 && Math.abs(y - label.y) < 25)
      );
      if (showLabel) labels.push({ x, y });
      return [{ source, point, showLabel }];
    });
  }, [sources, width, height, wcs, zoom, pan, imageWidth, selected]);

  if (!points.length) return null;
  return (
    <div className="catalog-overlay">
      {points.map(({ source, point, showLabel }) => (
        <button
          type="button"
          key={sourceKey(source)}
          className={"catalog-marker marker-" + source.catalog}
          style={{ left: (50 + point.x * 100) + "%", top: (50 + point.y * 100) + "%" }}
          aria-label={source.name}
          aria-pressed={selected === source}
          aria-describedby={selected === source ? tooltipId : undefined}
          onClick={() => onSelect(source)}
          title={source.name}
        >
          <i aria-hidden="true" />
          {showLabel && <span id={selected === source ? tooltipId : undefined}>{source.name}</span>}
        </button>
      ))}
    </div>
  );
}

export function SourceDetails({ source, language, t, clear }: {
  source: CatalogSource;
  language: string;
  t: Messages;
  clear: () => void;
}) {
  return (
    <section className="source-details" aria-label={t.catalogSource}>
      <div><h3>{source.name}</h3><button type="button" onClick={clear} aria-label={t.close}>×</button></div>
      <dl className="details-list">
        <div><dt>{t.catalogSource}</dt><dd>{catalogLabels[source.catalog]}</dd></div>
        <div><dt>RA</dt><dd>{source.ra.toLocaleString(language, { maximumFractionDigits: 6 })}°</dd></div>
        <div><dt>Dec</dt><dd>{source.dec.toLocaleString(language, { maximumFractionDigits: 6 })}°</dd></div>
        {source.magnitude !== undefined && (
          <div><dt>{t.magnitude} ({source.mag_band})</dt><dd>{source.magnitude.toLocaleString(language, { maximumFractionDigits: 2 })}</dd></div>
        )}
        {typeof source.details?.object_type === "string" && (
          <div><dt>{t.objectType}</dt><dd>{source.details.object_type}</dd></div>
        )}
      </dl>
    </section>
  );
}
