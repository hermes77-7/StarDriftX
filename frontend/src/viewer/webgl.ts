import { contrastRange, contrastSteps, palettes } from "./image";
import type { DisplaySettings } from "./image";

const vertexSource = `#version 300 es
void main() {
  vec2 corner = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  gl_Position = vec4(corner * 2.0 - 1.0, 0.0, 1.0);
}`;

function shaderPalette(colors: number[][]) {
  return colors
    .map((rgb) => `vec3(${rgb.map((value) => value.toFixed(1)).join(", ")})`)
    .join(", ");
}

const fragmentSource = `#version 300 es
precision highp float;
precision highp sampler2D;
uniform sampler2D image;
uniform sampler2D validity;
uniform vec2 clipRange;
uniform float strength;
uniform int stretch;
uniform int colormap;
uniform vec2 framebuffer;
out vec4 color;

const vec3 viridis[9] = vec3[9](${shaderPalette(palettes.viridis)});
const vec3 diverging[9] = vec3[9](${shaderPalette(palettes.diverging)});

float asinhPositive(float value) {
  return log(value + sqrt(1.0 + value * value));
}

float applyStretch(float value) {
  if (stretch == 1) return log(1.0 + strength * value) / log(1.0 + strength);
  if (stretch == 2) return asinhPositive(strength * value) / asinhPositive(strength);
  return value;
}

void main() {
  // Interpolate display brightness while keeping the observed footprint.
  ivec2 dimensions = textureSize(image, 0);
  vec2 location = gl_FragCoord.xy / framebuffer * vec2(dimensions) - 0.5;
  ivec2 closest = clamp(ivec2(floor(location + 0.5)), ivec2(0), dimensions - 1);
  if (texelFetch(validity, closest, 0).r == 0.0) {
    color = vec4(0.0);
    return;
  }
  ivec2 origin = ivec2(floor(location));
  vec2 fraction = fract(location);
  float pixel = 0.0;
  float weights = 0.0;
  for (int y = 0; y < 2; y++) {
    for (int x = 0; x < 2; x++) {
      ivec2 neighbor = clamp(origin + ivec2(x, y), ivec2(0), dimensions - 1);
      if (texelFetch(validity, neighbor, 0).r > 0.0) {
        float weight = (x == 0 ? 1.0 - fraction.x : fraction.x)
                     * (y == 0 ? 1.0 - fraction.y : fraction.y);
        pixel += weight * texelFetch(image, neighbor, 0).r;
        weights += weight;
      }
    }
  }
  pixel /= weights;

  float value = clipRange.y > 0.0 ? clamp((pixel - clipRange.x) / clipRange.y, 0.0, 1.0) : 0.5;
  value = floor(value * ${contrastSteps}.0 + 0.5) / ${contrastSteps}.0;
  if (colormap == 2) {
    float signedValue = value * 2.0 - 1.0;
    value = (sign(signedValue) * applyStretch(abs(signedValue)) + 1.0) * 0.5;
  } else {
    value = applyStretch(value);
  }
  if (colormap == 0) {
    color = vec4(vec3(floor(value * 255.0 + 0.5) / 255.0), 1.0);
    return;
  }
  float position = clamp(value, 0.0, 1.0) * 8.0;
  int lower = int(position);
  int upper = min(lower + 1, 8);
  vec3 rgb = colormap == 1
    ? mix(viridis[lower], viridis[upper], fract(position))
    : mix(diverging[lower], diverging[upper], fract(position));
  color = vec4(roundEven(rgb) / 255.0, 1.0);
}`;

function compile(gl: WebGL2RenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
  gl.deleteShader(shader);
  return null;
}

export function createWebGLRenderer(canvas: HTMLCanvasElement) {
  const gl = canvas.getContext("webgl2", {
    alpha: true,
    antialias: false,
    depth: false,
    stencil: false,
  });
  if (!gl || gl.isContextLost()) return null;

  const vertex = compile(gl, gl.VERTEX_SHADER, vertexSource);
  const fragment = compile(gl, gl.FRAGMENT_SHADER, fragmentSource);
  const program = gl.createProgram();
  const texture = gl.createTexture();
  const validityTexture = gl.createTexture();

  const dispose = () => {
    gl.deleteTexture(texture);
    gl.deleteTexture(validityTexture);
    gl.deleteProgram(program);
  };
  if (program && vertex && fragment) {
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
  }
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (
    !program ||
    !texture ||
    !validityTexture ||
    !gl.getProgramParameter(program, gl.LINK_STATUS)
  ) {
    dispose();
    return null;
  }

  gl.useProgram(program);
  const clipRange = gl.getUniformLocation(program, "clipRange");
  const strength = gl.getUniformLocation(program, "strength");
  const stretch = gl.getUniformLocation(program, "stretch");
  const colormap = gl.getUniformLocation(program, "colormap");
  const framebuffer = gl.getUniformLocation(program, "framebuffer");
  gl.uniform1i(gl.getUniformLocation(program, "image"), 0);
  gl.uniform1i(gl.getUniformLocation(program, "validity"), 1);
  for (const [unit, source] of [texture, validityTexture].entries()) {
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, source);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.disable(gl.DITHER);
  const maxSize = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number;

  return {
    setImage({
      width,
      height,
      pixels,
    }: {
      width: number;
      height: number;
      pixels: Float32Array;
    }) {
      if (width > maxSize || height > maxSize) return false;
      gl.activeTexture(gl.TEXTURE0);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.R32F,
        width,
        height,
        0,
        gl.RED,
        gl.FLOAT,
        pixels,
      );
      // Graphics drivers can canonicalize NaNs, so upload the validity explicitly.
      const valid = Uint8Array.from(pixels, (pixel) =>
        Number.isFinite(pixel) ? 255 : 0,
      );
      gl.activeTexture(gl.TEXTURE1);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.R8,
        width,
        height,
        0,
        gl.RED,
        gl.UNSIGNED_BYTE,
        valid,
      );
      return gl.getError() === gl.NO_ERROR;
    },
    draw(sorted: Float32Array, settings: DisplaySettings) {
      const { minimum, span } = contrastRange(sorted, settings);
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.uniform2f(framebuffer, canvas.width, canvas.height);
      gl.uniform2f(clipRange, minimum, span);
      gl.uniform1f(strength, settings.strength);
      gl.uniform1i(stretch, { linear: 0, log: 1, asinh: 2 }[settings.stretch]);
      gl.uniform1i(
        colormap,
        { grayscale: 0, viridis: 1, diverging: 2 }[settings.colormap],
      );
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
    dispose,
  };
}
