/**
 * Star Drift Standalone Mock Server (Node.js)
 * Serves mock API endpoints on http://127.0.0.1:8000
 *
 * Usage:
 *   node shared/mock_server.mjs
 */

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FIXTURE_DIR = path.resolve(__dirname, 'fixtures', 'mock');
const PORT = process.env.PORT || 8000;

function sendJson(res, status, data) {
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': '*',
  });
  res.end(JSON.stringify(data, null, 2));
}

function sendBinary(res, buffer) {
  res.writeHead(200, {
    'Content-Type': 'application/octet-stream',
    'Access-Control-Allow-Origin': '*',
    'Cache-Control': 'public, max-age=3600',
  });
  res.end(buffer);
}

function queryError(res, field, code, message) {
  return sendJson(res, 400, { error: { code, message, details: { field } } });
}

function handleRequest(req, res) {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Allow-Headers': '*',
    });
    return res.end();
  }

  const url = new URL(req.url, `http://${req.headers.host}`);
  const pathname = url.pathname;
  const params = url.searchParams;

  if (['/api/cutout', '/api/flags', '/api/spectrum', '/api/catalog'].includes(pathname)) {
    const numeric = [
      ['ra', 0, 360, 'INVALID_COORDINATES', false, false],
      ['dec', -90, 90, 'INVALID_COORDINATES', false, true],
      ...(pathname === '/api/cutout' || pathname === '/api/flags'
        ? [['fov', 0.05, 1, 'FOV_OUT_OF_RANGE'], ['band', 1, 102, 'INVALID_BAND', true],
           ['detector', 1, 6, 'INVALID_DETECTOR', true]]
        : pathname === '/api/catalog'
          ? [['radius', 0.01, 1, 'INVALID_PARAMETER'], ['mag_limit', -Infinity, Infinity]]
          : [['aperture', -Infinity, Infinity]]),
    ];
    for (const [field, min, max, code = 'INVALID_PARAMETER', integer, inclusive = true] of numeric) {
      const raw = params.get(field);
      if (raw === null && field !== 'ra' && field !== 'dec') continue;
      const value = Number(raw);
      if (!raw?.trim() || !Number.isFinite(value) || value < min ||
          (inclusive ? value > max : value >= max) || (integer && !Number.isInteger(value))) {
        return queryError(res, field, code, `Invalid value for ${field}.`);
      }
    }
    const choices = pathname === '/api/catalog'
      ? [['catalog', ['gaia', 'wise', 'simbad'], 'INVALID_PARAMETER']]
      : [['epoch', ['1', '2', '3', '4', 'all'], 'INVALID_EPOCH']];
    if (pathname === '/api/cutout') {
      choices.push(['mode', ['single', 'stack'], 'INVALID_PARAMETER']);
      choices.push(['format', ['binary', 'json'], 'INVALID_PARAMETER']);
      choices.push(['mask_flags',
        ['1', '0', 'true', 'false', 'on', 'off', 'yes', 'no', 'y', 'n', 't', 'f'],
        'INVALID_PARAMETER']);
    }
    for (const [field, allowed, code] of choices) {
      const raw = params.get(field);
      const value = field === 'mask_flags' ? raw?.toLowerCase() : raw;
      if (raw !== null && !allowed.includes(value)) {
        return queryError(res, field, code, `Invalid value for ${field}.`);
      }
    }
  }

  if (pathname === '/api/health') {
    return sendJson(res, 200, { status: 'ok' });
  }

  if (pathname === '/api/meta') {
    const meta = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, 'meta.json'), 'utf8'));
    return sendJson(res, 200, meta);
  }

  if (pathname === '/api/cutout') {
    if (params.get('format') === 'json') {
      return sendJson(res, 200, JSON.parse(
        fs.readFileSync(path.join(FIXTURE_DIR, 'cutout_scope.json'), 'utf8'),
      ));
    }

    const binaryBuffer = fs.readFileSync(path.join(FIXTURE_DIR, 'cutout_scope.bin'));
    return sendBinary(res, binaryBuffer);
  }

  if (pathname === '/api/flags') {
    const flags = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, 'flags_scope.json'), 'utf8'));
    return sendJson(res, 200, flags);
  }

  if (pathname === '/api/spectrum') {
    const spectrum = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, 'spectrum_scope.json'), 'utf8'));
    spectrum.ra = Number(params.get('ra'));
    spectrum.dec = Number(params.get('dec'));
    spectrum.aperture_arcsec = Number(params.get('aperture') ?? 6.15);
    spectrum.epoch = params.get('epoch') ?? 'all';
    return sendJson(res, 200, spectrum);
  }

  if (pathname === '/api/catalog') {
    const catalogName = params.get('catalog') || 'gaia';
    const catalogData = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, 'catalog_scope.json'), 'utf8'));
    const limit = Number(params.get('mag_limit') ?? Infinity);
    const filtered = catalogData.sources.filter(
      (source) => source.catalog === catalogName &&
        (source.magnitude == null || source.magnitude <= limit),
    );
    return sendJson(res, 200, {
      catalog: catalogName,
      count: filtered.length,
      sources: filtered,
    });
  }

  if (pathname === '/api/resolve') {
    const name = params.get('name') || '';
    if (!name.trim()) {
      return queryError(res, 'name', 'INVALID_PARAMETER', 'Enter an object name.');
    }
    const samples = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, 'resolve_samples.json'), 'utf8'));
    for (const [key, val] of Object.entries(samples)) {
      if (key.toLowerCase() === name.trim().toLowerCase()) {
        return sendJson(res, 200, val);
      }
    }
    return sendJson(res, 404, {
      error: {
        code: 'OBJECT_NOT_FOUND',
        message: `Object '${name}' not found.`,
      },
    });
  }

  sendJson(res, 404, {
    error: {
      code: 'NOT_FOUND',
      message: `Endpoint ${pathname} not found.`,
    },
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = http.createServer(handleRequest);
  server.listen(Number(PORT), '127.0.0.1', () => {
    console.log(`Star Drift mock server listening on http://127.0.0.1:${server.address().port}`);
  });
}
