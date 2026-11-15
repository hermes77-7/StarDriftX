import { describe, expect, it } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';
import type {
  MetaResponse,
  CutoutJsonResponse,
  FlagsResponse,
  SpectrumResponse,
  CatalogResponse,
  ResolveResponse,
} from '../types/api';

const FIXTURE_DIR = path.resolve(__dirname, '../../../shared/fixtures/mock');

describe('Mock API Contract and Fixtures (Ticket S3)', () => {
  it('validates meta.json conforms to MetaResponse schema', () => {
    const meta: MetaResponse = JSON.parse(
      fs.readFileSync(path.join(FIXTURE_DIR, 'meta.json'), 'utf8')
    );
    expect(meta.data_release).toBe('spherex_qr3');
    expect(meta.fov_limits.min_deg).toBe(0.05);
    expect(meta.fov_limits.max_deg).toBe(1.0);
    expect(meta.supported_bands.total_bands).toBe(102);
    expect(meta.supported_bands.detectors).toBe(6);
    expect(meta.epochs.length).toBeGreaterThanOrEqual(2);
  });

  it('validates cutout_scope.json conforms to CutoutJsonResponse schema', () => {
    const cutout: CutoutJsonResponse = JSON.parse(
      fs.readFileSync(path.join(FIXTURE_DIR, 'cutout_scope.json'), 'utf8')
    );
    expect(cutout.header.wcs.crval).toEqual([150.12, 2.34]);
    expect(cutout.header.wcs.shape).toEqual([36, 36]);
    expect(cutout.header.wavelength_um).toBe(0.809);
    expect(cutout.data.length).toBe(36);
    expect(cutout.data[0].length).toBe(36);
  });

  it('validates flags_scope.json conforms to FlagsResponse schema', () => {
    const flags: FlagsResponse = JSON.parse(
      fs.readFileSync(path.join(FIXTURE_DIR, 'flags_scope.json'), 'utf8')
    );
    expect(flags.total_pixels).toBe(1296);
    expect(flags.categories_present).toContain('known_source');
    expect(flags.flag_summary['known_source']).toBe(16);
  });

  it('validates spectrum_scope.json conforms to SpectrumResponse schema', () => {
    const spectrum: SpectrumResponse = JSON.parse(
      fs.readFileSync(path.join(FIXTURE_DIR, 'spectrum_scope.json'), 'utf8')
    );
    expect(spectrum.ra).toBe(150.12);
    expect(spectrum.dec).toBe(2.34);
    expect(spectrum.points.length).toBe(102);
    expect(spectrum.points[0].wavelength_um).toBeGreaterThanOrEqual(0.75);
    expect(spectrum.points[101].wavelength_um).toBeLessThanOrEqual(5.0);
  });

  it('validates catalog_scope.json conforms to CatalogResponse schema', () => {
    const catalog: CatalogResponse = JSON.parse(
      fs.readFileSync(path.join(FIXTURE_DIR, 'catalog_scope.json'), 'utf8')
    );
    expect(catalog.count).toBeGreaterThan(0);
    expect(catalog.sources[0].id).toBeDefined();
    expect(catalog.sources[0].ra).toBeCloseTo(150.12, 1);
  });

  it('validates resolve_samples.json conforms to ResolveResponse schema', () => {
    const samples: Record<string, ResolveResponse> = JSON.parse(
      fs.readFileSync(path.join(FIXTURE_DIR, 'resolve_samples.json'), 'utf8')
    );
    expect(samples['Vega']).toBeDefined();
    expect(samples['Vega'].ra).toBeCloseTo(279.23, 1);
    expect(samples['Vega'].dec).toBeCloseTo(38.78, 1);
  });
});
