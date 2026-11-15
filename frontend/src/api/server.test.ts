import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { spawn, type ChildProcess } from 'node:child_process';
import { once } from 'node:events';
import * as path from 'node:path';
import { decodePayload } from '../utils/payload';
import type {
  MetaResponse,
  FlagsResponse,
  SpectrumResponse,
  CatalogResponse,
  ResolveResponse,
  ErrorResponse,
} from '../types/api';

const mockServerPath = path.resolve(__dirname, '../../../shared/mock_server.mjs');
describe('Mock HTTP Server Endpoints (Ticket S3)', () => {
  let server: ChildProcess;
  let baseUrl: string;

  beforeAll(async () => {
    const child = spawn(process.execPath, [mockServerPath], {
      env: { ...process.env, PORT: '0' },
      stdio: ['ignore', 'pipe', 'inherit'],
    });
    server = child;
    const [message] = await Promise.race([
      once(child.stdout, 'data'),
      once(child, 'exit').then(() => { throw new Error('The Node mock exited during startup'); }),
    ]);
    const address = message.toString().match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];
    if (!address) throw new Error('The Node mock did not report a listening address');
    baseUrl = address;
  });

  afterAll(async () => {
    if (server?.exitCode === null) {
      const done = once(server, 'exit');
      server.kill();
      await done;
    }
  });

  it('GET /api/meta returns valid metadata', async () => {
    const res = await fetch(`${baseUrl}/api/meta`);
    expect(res.status).toBe(200);
    const data = (await res.json()) as MetaResponse;
    expect(data.data_release).toBe('spherex_qr3');
  });

  it('GET /api/cutout returns binary float payload and decodes successfully', async () => {
    const res = await fetch(`${baseUrl}/api/cutout?ra=150.12&dec=2.34&fov=0.2`);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('application/octet-stream');
    const arrayBuffer = await res.arrayBuffer();
    const payload = await decodePayload(arrayBuffer);
    expect(payload.header.wavelength_um).toBe(0.809);
    expect(payload.data.length).toBe(1296);
  });

  it('GET /api/cutout returns 400 for out-of-range coordinates', async () => {
    const res = await fetch(`${baseUrl}/api/cutout?ra=450&dec=2.34`);
    expect(res.status).toBe(400);
    const err = (await res.json()) as ErrorResponse;
    expect(err.error.code).toBe('INVALID_COORDINATES');
  });

  it('GET /api/flags returns quality flags', async () => {
    const res = await fetch(`${baseUrl}/api/flags?ra=150.12&dec=2.34`);
    expect(res.status).toBe(200);
    const data = (await res.json()) as FlagsResponse;
    expect(data.categories_present).toContain('known_source');
  });

  it('GET /api/spectrum returns 102 channels', async () => {
    const res = await fetch(`${baseUrl}/api/spectrum?ra=150.12&dec=2.34`);
    expect(res.status).toBe(200);
    const data = (await res.json()) as SpectrumResponse;
    expect(data.points.length).toBe(102);
  });

  it('GET /api/catalog returns catalog items', async () => {
    const res = await fetch(`${baseUrl}/api/catalog?ra=150.12&dec=2.34&radius=0.1&catalog=gaia`);
    expect(res.status).toBe(200);
    const data = (await res.json()) as CatalogResponse;
    expect(data.sources.length).toBeGreaterThan(0);
  });

  it('GET /api/resolve resolves Vega and returns 404 for unknown object', async () => {
    const resVega = await fetch(`${baseUrl}/api/resolve?name=Vega`);
    expect(resVega.status).toBe(200);
    const vega = (await resVega.json()) as ResolveResponse;
    expect(vega.ra).toBeCloseTo(279.23, 1);

    const resUnknown = await fetch(`${baseUrl}/api/resolve?name=NonexistentStar12345`);
    expect(resUnknown.status).toBe(404);
    const err = (await resUnknown.json()) as ErrorResponse;
    expect(err.error.code).toBe('OBJECT_NOT_FOUND');
  });
});
