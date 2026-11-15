export type Stretch = "linear" | "log" | "asinh";
export type Colormap = "grayscale" | "viridis" | "diverging";
export type DisplaySettings = {
  stretch: Stretch;
  low: number;
  high: number;
  strength: number;
  colormap: Colormap;
  subtractBackground: boolean;
};

export const defaultStrength = { linear: 1, log: 100, asinh: 8 };
export const strengthRange = { min: 1, max: 150 };
export const clipStep = 0.5;

export const defaultDisplay: DisplaySettings = {
  stretch: "asinh",
  low: 1,
  high: 99,
  strength: defaultStrength.asinh,
  colormap: "grayscale",
  subtractBackground: false,
};

function percentile(sorted: Float32Array | number[], percent: number) {
  if (sorted.length === 0) return 0;
  const index = ((sorted.length - 1) * percent) / 100;
  const lower = Math.floor(index);
  return (
    sorted[lower] + (sorted[Math.ceil(index)] - sorted[lower]) * (index - lower)
  );
}

export function subtractBackgroundGradient(
  pixels: Float32Array,
  width: number,
  height: number,
) {
  const horizontal: number[] = [];
  const vertical: number[] = [];
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const index = y * width + x;
      const pixel = pixels[index];
      if (!Number.isFinite(pixel)) continue;
      if (x > 0 && Number.isFinite(pixels[index - 1])) {
        horizontal.push(pixel - pixels[index - 1]);
      }
      if (y > 0 && Number.isFinite(pixels[index - width])) {
        vertical.push(pixel - pixels[index - width]);
      }
    }
  }

  // Median neighbor differences estimate a plane while limiting the effect of bright sources.
  const slopeX = percentile(
    horizontal.sort((a, b) => a - b),
    50,
  );
  const slopeY = percentile(
    vertical.sort((a, b) => a - b),
    50,
  );
  const centerX = (width - 1) / 2;
  const centerY = (height - 1) / 2;
  const flattened = pixels.map(
    (pixel, index) =>
      pixel -
      (((index % width) - centerX) * slopeX +
        (Math.floor(index / width) - centerY) * slopeY),
  );
  const background = percentile(flattened.filter(Number.isFinite).sort(), 50);
  return flattened.map((pixel) => pixel - background);
}

function stretchFunction({ stretch, strength }: DisplaySettings) {
  switch (stretch) {
    case "log": {
      const maximum = Math.log1p(strength);
      return (value: number) => Math.log1p(strength * value) / maximum;
    }
    case "asinh": {
      const maximum = Math.asinh(strength);
      return (value: number) => Math.asinh(strength * value) / maximum;
    }
    case "linear":
      return (value: number) => value;
  }
}

// Viridis samples from Matplotlib, spaced evenly in the normalized intensity range.
const viridis = [
  [68, 1, 84],
  [71, 45, 123],
  [59, 82, 139],
  [44, 114, 142],
  [33, 145, 140],
  [40, 174, 128],
  [94, 201, 98],
  [173, 220, 48],
  [253, 231, 37],
];

// Negative differences are violet, positive differences are amber, and zero is dark.
const diverging = [
  [221, 205, 255],
  [177, 143, 229],
  [122, 85, 170],
  [66, 43, 97],
  [20, 20, 23],
  [100, 60, 31],
  [166, 108, 51],
  [220, 163, 79],
  [255, 221, 147],
];

export const palettes = { grayscale: null, viridis, diverging };
export const contrastSteps = 4096;

export function contrastRange(sorted: Float32Array, settings: DisplaySettings) {
  const minimum = percentile(sorted, settings.low);
  const maximum = percentile(sorted, settings.high);
  if (settings.colormap === "diverging") {
    // A sparse residual can leave both clip percentiles at zero.
    const extent = Math.max(Math.abs(minimum), Math.abs(maximum)) ||
      (sorted.length ? Math.max(Math.abs(sorted[0]), Math.abs(sorted[sorted.length - 1])) : 0);
    return { minimum: -extent, span: extent * 2 };
  }
  return { minimum, span: maximum - minimum };
}

function colorTable(settings: DisplaySettings) {
  const steps = contrastSteps;
  const table = new Uint8ClampedArray((steps + 1) * 4);
  const stretch = stretchFunction(settings);
  const palette = palettes[settings.colormap];
  for (let index = 0; index <= steps; index++) {
    const normalized = index / steps;
    const signed = normalized * 2 - 1;
    const value =
      settings.colormap === "diverging"
        ? (Math.sign(signed) * stretch(Math.abs(signed)) + 1) / 2
        : stretch(normalized);
    const offset = index * 4;
    if (palette) {
      const position = value * (palette.length - 1);
      const lower = palette[Math.floor(position)];
      const upper = palette[Math.ceil(position)];
      for (let channel = 0; channel < 3; channel++) {
        table[offset + channel] =
          lower[channel] + (upper[channel] - lower[channel]) * (position % 1);
      }
    } else {
      const intensity = Math.round(value * 255);
      table[offset] = table[offset + 1] = table[offset + 2] = intensity;
    }
    table[offset + 3] = 255;
  }
  return { table: new Uint32Array(table.buffer), steps };
}

export function drawPixels(
  {
    width,
    height,
    pixels,
  }: { width: number; height: number; pixels: Float32Array },
  sorted: Float32Array,
  settings: DisplaySettings,
  rgba: Uint8ClampedArray,
) {
  const output = new Uint32Array(rgba.buffer, rgba.byteOffset, pixels.length);
  const { table, steps } = colorTable(settings);
  const { minimum, span } = contrastRange(sorted, settings);

  // FITS rows advance northward. Canvas rows start at the top of the image.
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const pixel = pixels[(height - 1 - y) * width + x];
      const offset = y * width + x;
      if (!Number.isFinite(pixel)) {
        output[offset] = 0;
        continue;
      }
      const normalized =
        span > 0 ? Math.max(0, Math.min(1, (pixel - minimum) / span)) : 0.5;
      output[offset] = table[Math.round(normalized * steps)];
    }
  }
}
