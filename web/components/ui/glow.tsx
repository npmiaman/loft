/**
 * The soft glow behind content, as a plain background on the element itself: no extra layers, no negative z-index, no
 * containment. (An earlier version built it from huge stacked elements and made some GPUs drop paint tiles: blank, half-drawn pages.)
 */
export const glow =
  "bg-[radial-gradient(60%_45%_at_80%_0%,color-mix(in_srgb,var(--ink)_6%,transparent),transparent_70%),radial-gradient(40%_40%_at_100%_30%,color-mix(in_srgb,var(--ink)_3%,transparent),transparent_70%)]";
