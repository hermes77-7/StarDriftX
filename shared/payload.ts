import type { CutoutBinaryPayload, CutoutHeader } from './types/api';

async function decompressGzip(bytes: Uint8Array<ArrayBuffer>): Promise<ArrayBuffer> {
  const source = new Response(bytes).body;
  if (!source) throw new Error('No compressed data stream');
  return new Response(source.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
}

export async function decodePayload(buffer: ArrayBuffer | Uint8Array): Promise<CutoutBinaryPayload> {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const decoder = new TextDecoder('utf-8', { fatal: true });
  if (decoder.decode(bytes.subarray(0, 4)) !== 'SPX1') {
    throw new Error('Invalid payload magic, expected SPX1');
  }

  let offset = 4;
  function section() {
    if (offset + 4 > bytes.length) throw new Error('Truncated SPX1 section length');
    const length = view.getUint32(offset, false);
    offset += 4;
    const end = offset + length;
    if (end > bytes.length) throw new Error('Truncated SPX1 section');
    const part = Uint8Array.from(bytes.subarray(offset, end));
    offset = end;
    return part;
  }

  const header = JSON.parse(decoder.decode(section())) as CutoutHeader;
  const shape = header?.wcs?.shape;
  if (header?.version !== '1.0' || !Array.isArray(shape) || shape.length !== 2 ||
      shape.some((side) => !Number.isSafeInteger(side) || side <= 0)) {
    throw new Error('Expected a version 1.0 header with positive image dimensions');
  }
  const pixels = shape[0] * shape[1];
  async function plane() {
    const raw = await decompressGzip(section());
    if (raw.byteLength !== pixels * 4) {
      throw new Error('Payload plane does not match the declared image dimensions');
    }
    return new DataView(raw);
  }

  const image = await plane();
  const data = Float32Array.from({ length: pixels }, (_, i) => image.getFloat32(i * 4, true));
  const flagPlane = offset < bytes.length ? await plane() : undefined;
  const flags = flagPlane &&
    Uint32Array.from({ length: pixels }, (_, i) => flagPlane.getUint32(i * 4, true));
  if (offset !== bytes.length) throw new Error('Unexpected bytes after SPX1 payload');
  return { header, data, flags };
}
