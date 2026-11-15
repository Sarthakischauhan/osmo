/**
 * WebGL orb. A disc in the fragment shader, displaced by a few sines so the edge
 * ripples with the voice level. No buffers beyond a fullscreen triangle: the canvas
 * is the whole draw. Falls back to the 2D blob when WebGL is missing.
 */

import { type OrbFrameState, type OrbMode } from "./orbVisual";

const VERT = `
attribute vec2 a;
void main() { gl_Position = vec4(a, 0.0, 1.0); }
`;

const FRAG = `
precision mediump float;
uniform vec2 uRes;
uniform float uTime;
uniform float uLevel;
uniform float uWobble;
uniform float uGlow;
uniform vec3 uLight;
uniform vec3 uMid;
uniform vec3 uDeep;

float edge(float a, float t, float amount) {
  float n = sin(2.0 * a + t * 1.3) * 0.55
    + sin(3.0 * a - t * 1.7) * 0.3
    + sin(5.0 * a + t * 2.4) * (0.12 + uLevel * 0.35);
  return 1.0 + n * amount;
}

void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
  float a = atan(p.y, p.x);
  float r = length(p);
  float radius = 0.34 * (1.0 + uLevel * 0.28);
  float amount = (uWobble * 0.55 + uLevel * 0.85) * 0.22;
  float e = edge(a, uTime, amount);
  float body = radius * e;
  if (r > body * 1.55) { gl_FragColor = vec4(0.0); return; }
  float glow = smoothstep(body * 1.55, body * 0.72, r) * uGlow * (0.55 + uLevel * 0.7);
  float inside = smoothstep(body, body - 0.012, r);
  vec2 swirl = vec2(cos(uTime * 1.4 - 2.3), sin(uTime * 1.4 - 2.3)) * radius * 0.32;
  float h = clamp(length(p - swirl) / max(radius, 0.001), 0.0, 1.0);
  vec3 color = mix(uLight, uMid, smoothstep(0.0, 0.45, h));
  color = mix(color, uDeep, smoothstep(0.35, 1.0, h));
  color = mix(color, vec3(1.0), (1.0 - h) * (0.35 + uLevel * 0.4));
  vec3 outColor = mix(uMid * glow, color, inside);
  float alpha = max(glow * 0.85, inside);
  gl_FragColor = vec4(outColor, alpha);
}
`;

function compile(gl: WebGLRenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) throw new Error("shader");
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader) ?? "";
    gl.deleteShader(shader);
    throw new Error(log);
  }
  return shader;
}

export interface OrbGl {
  draw(size: number, frame: OrbFrameState, mode: OrbMode): void;
  lost: () => boolean;
}

/** A WebGL context drawing the voice orb, or null when the canvas cannot do WebGL. */
export function createOrbGl(canvas: HTMLCanvasElement): OrbGl | null {
  const gl = canvas.getContext("webgl", { alpha: true, premultipliedAlpha: false, antialias: true });
  if (!gl) return null;
  let program: WebGLProgram;
  try {
    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
    const linked = gl.createProgram();
    if (!linked) return null;
    program = linked;
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.bindAttribLocation(program, 0, "a");
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  } catch {
    return null;
  }
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  // One triangle covering the clip square.
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  const loc = {
    res: gl.getUniformLocation(program, "uRes"),
    time: gl.getUniformLocation(program, "uTime"),
    level: gl.getUniformLocation(program, "uLevel"),
    wobble: gl.getUniformLocation(program, "uWobble"),
    glow: gl.getUniformLocation(program, "uGlow"),
    light: gl.getUniformLocation(program, "uLight"),
    mid: gl.getUniformLocation(program, "uMid"),
    deep: gl.getUniformLocation(program, "uDeep"),
  };
  return {
    lost: () => gl.isContextLost(),
    draw(size, frame, _mode) {
      const { preset, level, time } = frame;
      const [light, mid, deep] = preset.colors;
      gl.viewport(0, 0, size, size);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(program);
      gl.uniform2f(loc.res, size, size);
      gl.uniform1f(loc.time, time);
      gl.uniform1f(loc.level, level);
      gl.uniform1f(loc.wobble, preset.wobble);
      gl.uniform1f(loc.glow, preset.glow);
      gl.uniform3f(loc.light, light[0] / 255, light[1] / 255, light[2] / 255);
      gl.uniform3f(loc.mid, mid[0] / 255, mid[1] / 255, mid[2] / 255);
      gl.uniform3f(loc.deep, deep[0] / 255, deep[1] / 255, deep[2] / 255);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
  };
}
