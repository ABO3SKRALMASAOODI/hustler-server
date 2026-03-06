import { useState, useRef } from "react";

const MIN = -999;
const MAX = 999;

export default function Counter() {
  const [count, setCount] = useState(0);
  const [animDir, setAnimDir] = useState<"up" | "down" | null>(null);
  const animTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const triggerAnim = (dir: "up" | "down") => {
    if (animTimeout.current) clearTimeout(animTimeout.current);
    setAnimDir(dir);
    animTimeout.current = setTimeout(() => setAnimDir(null), 200);
  };

  const increment = () => {
    if (count >= MAX) return;
    triggerAnim("up");
    setCount((c) => c + 1);
  };

  const decrement = () => {
    if (count <= MIN) return;
    triggerAnim("down");
    setCount((c) => c - 1);
  };

  const reset = () => {
    setAnimDir(null);
    setCount(0);
  };

  const isPositive = count > 0;
  const isNegative = count < 0;

  const digitColor = isPositive
    ? "text-primary"
    : isNegative
    ? "text-destructive"
    : "text-foreground";

  const scaleClass =
    animDir === "up"
      ? "scale-110"
      : animDir === "down"
      ? "scale-90"
      : "scale-100";

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-10">

        {/* Title */}
        <h1 className="text-muted-foreground text-sm font-semibold uppercase tracking-[0.3em]">
          Counter
        </h1>

        {/* Display */}
        <div
          className="flex items-center justify-center rounded-2xl border border-border bg-card shadow-2xl"
          style={{ width: "min(88vw, 380px)", height: "min(40vw, 180px)" }}
        >
          <span
            className={`font-mono font-black transition-transform duration-150 ease-out ${digitColor} ${scaleClass}`}
            style={{ fontSize: "clamp(4rem, 16vw, 8rem)", lineHeight: 1, letterSpacing: "-0.04em" }}
          >
            {count > 0 ? `+${count}` : count}
          </span>
        </div>

        {/* Buttons */}
        <div className="flex items-center gap-4">
          {/* Decrement */}
          <button
            onClick={decrement}
            disabled={count <= MIN}
            aria-label="Decrement"
            className="
              flex h-16 w-16 items-center justify-center rounded-xl
              border border-border bg-card text-foreground text-3xl font-bold
              transition-all duration-100
              hover:border-primary hover:text-primary hover:bg-primary/10
              active:scale-95
              disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:border-border disabled:hover:text-foreground disabled:hover:bg-card
            "
          >
            −
          </button>

          {/* Reset */}
          <button
            onClick={reset}
            aria-label="Reset"
            className="
              flex h-10 w-20 items-center justify-center rounded-lg
              border border-border bg-card text-muted-foreground text-xs font-semibold uppercase tracking-widest
              transition-all duration-100
              hover:border-ring hover:text-foreground hover:bg-secondary
              active:scale-95
            "
          >
            Reset
          </button>

          {/* Increment */}
          <button
            onClick={increment}
            disabled={count >= MAX}
            aria-label="Increment"
            className="
              flex h-16 w-16 items-center justify-center rounded-xl
              border border-border bg-card text-foreground text-3xl font-bold
              transition-all duration-100
              hover:border-primary hover:text-primary hover:bg-primary/10
              active:scale-95
              disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:border-border disabled:hover:text-foreground disabled:hover:bg-card
            "
          >
            +
          </button>
        </div>

        {/* Range hint */}
        <p className="text-muted-foreground text-xs tracking-wide">
          Range: {MIN} — {MAX}
        </p>
      </div>
    </div>
  );
}
