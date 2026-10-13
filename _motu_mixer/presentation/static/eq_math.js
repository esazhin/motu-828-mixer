// EQ response, ported from CueMix 5 equi.js (EQBand.CalculateCoeff + GenerateCurve) so the curve matches CueMix's:
// RBJ-style biquads; peak uses Q, shelves a fixed sqrt(2) slope, high-pass a fixed Butterworth (gain and Q unused).
export const MODES = ['peak', 'lowShelf', 'highShelf', 'highPass'];
export const FREQ_MIN = 20;
export const FREQ_MAX = 20000;
export const GAIN_MAX = 20;
export const Q_MIN = 0.45;
export const Q_MAX = 10;

export function coefficients({ mode, freq, gain, q }, fs = 48000) {
  const V = Math.pow(10, Math.abs(gain) / 20);
  const K = Math.tan(Math.PI * freq / fs);
  const kQ = Math.SQRT2 / 2;
  const S2 = Math.SQRT2;
  let norm;
  switch (MODES[mode]) {
    case 'highPass':
      norm = 1 / (1 + K / kQ + K * K);
      return { a0: norm, a1: -2 * norm, a2: norm, b1: 2 * (K * K - 1) * norm, b2: (1 - K / kQ + K * K) * norm };
    case 'lowShelf':
      if (gain >= 0) {
        norm = 1 / (1 + S2 * K + K * K);
        return { a0: (1 + Math.sqrt(2 * V) * K + V * K * K) * norm, a1: 2 * (V * K * K - 1) * norm,
          a2: (1 - Math.sqrt(2 * V) * K + V * K * K) * norm, b1: 2 * (K * K - 1) * norm, b2: (1 - S2 * K + K * K) * norm };
      }
      norm = 1 / (1 + Math.sqrt(2 * V) * K + V * K * K);
      return { a0: (1 + S2 * K + K * K) * norm, a1: 2 * (K * K - 1) * norm, a2: (1 - S2 * K + K * K) * norm,
        b1: 2 * (V * K * K - 1) * norm, b2: (1 - Math.sqrt(2 * V) * K + V * K * K) * norm };
    case 'highShelf':
      if (gain >= 0) {
        norm = 1 / (1 + S2 * K + K * K);
        return { a0: (V + Math.sqrt(2 * V) * K + K * K) * norm, a1: 2 * (K * K - V) * norm,
          a2: (V - Math.sqrt(2 * V) * K + K * K) * norm, b1: 2 * (K * K - 1) * norm, b2: (1 - S2 * K + K * K) * norm };
      }
      norm = 1 / (V + Math.sqrt(2 * V) * K + K * K);
      return { a0: (1 + S2 * K + K * K) * norm, a1: 2 * (K * K - 1) * norm, a2: (1 - S2 * K + K * K) * norm,
        b1: 2 * (K * K - V) * norm, b2: (V - Math.sqrt(2 * V) * K + K * K) * norm };
    default: // peak
      if (gain >= 0) {
        norm = 1 / (1 + K / q + K * K);
        return { a0: (1 + V / q * K + K * K) * norm, a1: 2 * (K * K - 1) * norm, a2: (1 - V / q * K + K * K) * norm,
          b1: 2 * (K * K - 1) * norm, b2: (1 - K / q + K * K) * norm };
      }
      norm = 1 / (1 + V / q * K + K * K);
      return { a0: (1 + K / q + K * K) * norm, a1: 2 * (K * K - 1) * norm, a2: (1 - K / q + K * K) * norm,
        b1: 2 * (K * K - 1) * norm, b2: (1 - V / q * K + K * K) * norm };
  }
}

// |H| in dB at frequency f (the closed form CueMix uses, with phi = sin^2(w/2)).
export function bandDb(c, f, fs = 48000) {
  const phi = Math.pow(Math.sin(Math.PI * f / fs), 2);
  const num = (c.a0 + c.a1 + c.a2) ** 2 - 4 * (c.a0 * c.a1 + 4 * c.a0 * c.a2 + c.a1 * c.a2) * phi + 16 * c.a0 * c.a2 * phi * phi;
  const den = (1 + c.b1 + c.b2) ** 2 - 4 * (c.b1 + 4 * c.b2 + c.b1 * c.b2) * phi + 16 * c.b2 * phi * phi;
  const db = 10 * Math.log10(num / den);
  return Number.isFinite(db) ? db : -100;
}

export function logFreqs(n = 96) {
  return Array.from({ length: n }, (_, i) => FREQ_MIN * Math.pow(FREQ_MAX / FREQ_MIN, i / (n - 1)));
}

// The summed response of the active (not bypassed) bands at each frequency.
export function response(bands, freqs = logFreqs(), fs = 48000) {
  const active = bands.filter((b) => !b.bypass && b.freq).map((b) => coefficients(b, fs));
  return freqs.map((f) => active.reduce((sum, c) => sum + bandDb(c, f, fs), 0));
}
