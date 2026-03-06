import { useState } from "react";

const MIN = -999;
const MAX = 999;

export default function Counter() {
  const [count, setCount] = useState(0);

  const increment = () => setCount((c) => Math.min(c + 1, MAX));
  const decrement = () => setCount((c) => Math.max(c - 1, MIN));
  const reset = () => setCount(0);

  const isPositive = count > 0;
  const isNegative = count < 0;

  return (
    <div className="flex flex-col items-center justify-center gap-10 select-none">

      {/* Label */}
      <p className="text-muted-foreground tracking-[0.3em] uppercase text-sm font-medium">
        Counter
      </p>

      {/* Count Display */}
      <div
        className="relative flex items-center justify-center w-56 h-56 rounded-full border border-border"
        style={{ boxShadow: "0 0 60px -10px hsl(var(--primary) / 0.25)" }}
      >
        {/* Glow ring */}
        <div
          className="absolute inset-0 rounded-full opacity-10"
          style={{ background: "radial-gradient(circle, hsl(var(--primary)) 0%, transparent 70%)" }}
        />

        <span
          className={[
            "relative z-10 text-7xl font-bold transition-colors duration-300",
            isPositive ? "text-primary" : isNegative ? "text-destructive" : "text-foreground",
          ].join(" ")}
          style={{ fontFamily: "var(--font-display)" }}
        >
          {count > 0 ? `+${count}` : count}
        </span>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-4">
        {/* Decrement */}
        <button
          onClick={decrement}
          disabled={count <= MIN}
          aria-label="Decrement"
          className="group flex items-center justify-center w-16 h-16 rounded-full border border-border bg-card text-foreground text-2xl font-light transition-all duration-150 hover:border-destructive hover:text-destructive hover:shadow-[0_0_20px_-4px_hsl(var(--destructive)/0.5)] disabled:opacity-25 disabled:cursor-not-allowed active:scale-95"
        >
          −
        </button>

        {/* Reset */}
        <button
          onClick={reset}
          aria-label="Reset"
          className="flex items-center justify-center px-6 h-10 rounded-full border border-border bg-card text-muted-foreground text-xs tracking-widest uppercase font-medium transition-all duration-150 hover:border-primary hover:text-primary hover:shadow-[0_0_16px_-4px_hsl(var(--primary)/0.4)] active:scale-95"
        >
          Reset
        </button>

        {/* Increment */}
        <button
          onClick={increment}
          disabled={count >= MAX}
          aria-label="Increment"
          className="group flex items-center justify-center w-16 h-16 rounded-full border border-border bg-card text-foreground text-2xl font-light transition-all duration-150 hover:border-primary hover:text-primary hover:shadow-[0_0_20px_-4px_hsl(var(--primary)/0.5)] disabled:opacity-25 disabled:cursor-not-allowed active:scale-95"
        >
          +
        </button>
      </div>

      {/* Hint */}
      <p className="text-muted-foreground text-xs tracking-wide">
        Range: {MIN} → {MAX}
      </p>
    </div>
  );
}
