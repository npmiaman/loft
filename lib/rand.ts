// Shared randomness. Human timings are right-skewed (many short, few long), not uniform => log-normal.
export const rand = (a: number, b: number) => a + Math.random() * (b - a);
export const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));

export function gauss(mean = 0, sd = 1): number {
  const u = 1 - Math.random();
  const v = Math.random();
  return mean + sd * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

/** Right-skewed sample with the given median. sigma ~0.3 = mild spread, ~0.6 = wide. */
export const lognormal = (median: number, sigma = 0.35) => median * Math.exp(sigma * gauss());
