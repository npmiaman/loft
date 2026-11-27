"use client";
import { MeshGradient } from "@paper-design/shaders-react";
import * as React from "react";

/** The one WebGL surface: a slow mesh-gradient shader behind the sign-in panel. Falls back to a flat colour if WebGL is unavailable. */
export function ShaderBg({ className }: { className?: string }) {
  const [reduced, setReduced] = React.useState(false);
  React.useEffect(() => { setReduced(window.matchMedia("(prefers-reduced-motion: reduce)").matches); }, []);
  return (
    <MeshGradient
      className={className}
      style={{ background: "#0d0d0d" }}
      colors={["#0d0d0d", "#0b6b55", "#10a37f", "#1d1d1f"]}
      distortion={0.9}
      swirl={0.35}
      speed={reduced ? 0 : 0.18}
      grainMixer={0.15}
      grainOverlay={0.12}
    />
  );
}
