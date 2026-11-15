/**
 * Star Drift API TypeScript Definitions
 * Generated from shared/openapi.yaml (OpenAPI 3.1.0)
 */

export type ErrorCode =
  | 'INVALID_COORDINATES'
  | 'FOV_OUT_OF_RANGE'
  | 'INVALID_BAND'
  | 'INVALID_EPOCH'
  | 'INVALID_DETECTOR'
  | 'INVALID_PARAMETER'
  | 'FORBIDDEN'
  | 'NO_COVERAGE'
  | 'OBJECT_NOT_FOUND'
  | 'SERVICE_UNAVAILABLE'
  | 'UPSTREAM_TIMEOUT'
  | 'RATE_LIMIT_EXCEEDED'
  | 'INTERNAL_ERROR';

export interface ErrorDetail {
  code: ErrorCode;
  message: string;
  details?: Record<string, unknown>;
}

export interface ErrorResponse {
  error: ErrorDetail;
}

export interface HealthResponse {
  status: string;
}

export interface FovLimits {
  min_deg: number;
  max_deg: number;
  default_deg: number;
}

export interface SupportedBands {
  total_bands: number;
  detectors: number;
  channels_per_detector: number;
  wavelength_range_um: [number, number];
  bands?: BandInfo[];
}

export interface BandInfo {
  band: number;
  detector: number;
  channel: number;
  wavelength_um: number;
}

export interface EpochTimeRange {
  mjd_start?: number;
  mjd_end?: number;
  utc_start?: string;
  utc_end?: string;
}

export interface EpochInfo {
  id: string;
  name: string;
  time_range: EpochTimeRange;
  collection?: string;
  pointing_angle_deg?: number;
}

export interface MetaResponse {
  data_release: string;
  coverage_notes: string;
  fov_limits: FovLimits;
  supported_bands: SupportedBands;
  epochs: EpochInfo[];
  collections?: string[];
  pixel_scale_arcsec?: number;
}

export interface WcsHeader {
  crval: [number, number];
  crpix: [number, number];
  cdelt: [number, number];
  ctype: [string, string];
  radesys: string;
  shape: [number, number];
}

export interface CutoutHeader {
  version: string;
  data_release: string;
  wcs: WcsHeader;
  bunit: string;
  detector?: number;
  channel?: number;
  band?: number;
  wavelength_um: number;
  bandwidth_um?: number;
  nexp?: number;
  epoch?: Epoch;
  mjd_range?: [number, number];
  flags_summary?: Record<string, number>;
  source?: 'irsa' | 'm31-dr1' | 'dr1-cube';
  collections?: string[];
  exposure_mjd?: number[];
  exposure_ids?: string[];
  exposures?: { id: string; collection: string; processing_version: string | null; mjd: number }[];
  wavelength_range_um?: [number, number];
  flagged_fraction?: number;
  coverage_fraction?: number;
  mask_flags?: boolean;
  photometric_normalized?: boolean;
  warnings?: string[];
}

export interface CutoutJsonResponse {
  header: CutoutHeader;
  data: (number | null)[][];
}

export interface CutoutBinaryPayload {
  header: CutoutHeader;
  data: Float32Array;
  flags?: Uint32Array;
}

export interface FlagsResponse {
  categories_present: string[];
  total_pixels: number;
  flagged_pixels: number;
  flag_summary: Record<string, number>;
  bitmask_definitions?: Record<string, string>;
}

export interface SpectrumPoint {
  wavelength_um: number;
  flux: number;
  flux_unit: string;
  uncertainty: number;
  exposure_id: string;
  detector: number;
  channel: number;
  band_index: number;
  flagged: boolean;
  flags: string[];
}

export interface SpectrumResponse {
  ra: number;
  dec: number;
  aperture_arcsec: number;
  epoch: string;
  points: SpectrumPoint[];
}

export type CatalogName = 'gaia' | 'wise' | 'simbad';
export type Epoch = '1' | '2' | '3' | '4' | 'all';

export interface CatalogSource {
  id: string;
  name: string;
  ra: number;
  dec: number;
  catalog: CatalogName;
  magnitude?: number;
  mag_band?: string;
  details?: Record<string, unknown>;
}

export interface CatalogResponse {
  catalog: CatalogName | string;
  count: number;
  sources: CatalogSource[];
  truncated?: boolean;
}

export interface ResolveResponse {
  name: string;
  ra: number;
  dec: number;
  resolver: string;
}

export interface CutoutQueryParams {
  ra: number;
  dec: number;
  fov?: number;
  epoch?: Epoch;
  band?: number;
  detector?: number;
  mode?: 'single' | 'stack';
  mask_flags?: boolean;
  format?: 'binary' | 'json';
  wait_for_live?: boolean;
}

export interface FlagsQueryParams {
  ra: number;
  dec: number;
  fov?: number;
  epoch?: Epoch;
  band?: number;
  detector?: number;
}

export interface SpectrumQueryParams {
  ra: number;
  dec: number;
  aperture?: number;
  epoch?: Epoch;
}

export interface CatalogQueryParams {
  ra: number;
  dec: number;
  radius?: number;
  catalog?: CatalogName;
  mag_limit?: number;
}

export interface ResolveQueryParams {
  name: string;
}
