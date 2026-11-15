import { describe, expect, it } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { decodePayload } from './payload';

describe('Binary Payload Decoder', () => {
  it('correctly decodes the mock SPX1 test vector cutout_scope.bin', async () => {
    const fixturePath = path.resolve(__dirname, '../../../shared/fixtures/mock/cutout_scope.bin');
    const buffer = fs.readFileSync(fixturePath);
    const decoded = await decodePayload(buffer);

    expect(decoded.header.version).toBe('1.0');
    expect(decoded.header.data_release).toBe('spherex_qr3');
    expect(decoded.header.wcs.crval).toEqual([150.12, 2.34]);
    expect(decoded.header.wcs.shape).toEqual([36, 36]);
    expect(decoded.header.wavelength_um).toBe(0.809);

    // Science floats (36 x 36 = 1296 pixels)
    expect(decoded.data.length).toBe(1296);
    expect(decoded.data[0]).toBeCloseTo(0.025, 2);

    // Flags
    expect(decoded.flags).toBeDefined();
    expect(decoded.flags?.length).toBe(1296);
    expect(decoded.flags?.[16 * 36 + 16]).toBe((1 << 21) | (1 << 28));
    expect(decoded.flags?.filter((flag) => (flag & (1 << 28)) !== 0)).toHaveLength(1);
  });

  it('rejects a truncated flag section', async () => {
    const file = path.resolve(__dirname, '../../../shared/fixtures/mock/cutout_scope.bin');
    const bytes = fs.readFileSync(file);
    await expect(decodePayload(bytes.subarray(0, -1))).rejects.toThrow(/Truncated SPX1/);
  });
});
