import { useState } from "react";

const MIN = -99;
const MAX = 999;

const Counter = () => {
  const [count, setCount] = useState(0);

  const increment = () => setCount((c) => Math.min(c + 1, MAX));
  const decrement = () => setCount((c) => Math.max(c - 1, MIN));
  const reset = () => setCount(0);

  const isPositive = count > 0;
  const isNegative = count < 0;

  return (
    <div className="flex flex-col items-center gap-10">
      {/* Title */}
      <div className="text-center">
        <h1 className="text-sm font-semibold tracking-[0.3em] uppercase text-muted-foreground">
          Counter
        </h1>
      </div>

      {/* Card */}
      <div className="relative flex flex-col items-center gap-8 rounded-3xl border border-border bg-card px-16 py-12 shadow-2xl">
        {/* Glow effect */}
        <div
          className="pointer-events-none absolute inset-0 rounded-3xl opacity-20 blur-3xl"
          style={{
            background: isPositive
              ? "hsl(14 90% 62%)"
              : isNegative
              ? "hsl(220 80% 60%)"
              : "hsl(222 35% 30%)",
          }}
        />

        {/* Count display */}
        <div className="relative z-10 flex items-center justify-center">
          <span
            className="font-mono font-black leading-none tabular-nums transition-all duration-200"
            style={{
              fontSize: "clamp(5rem, 20vw, 9rem)",
              color: isPositive
                ? "hsl(14 90% 62%)"
                : isNegative
                ? "hsl(220 80% 72%)"
                : "hsl(210 40% 96%)",
            }}
          >
            {count > 0 ? `+${count}` : count}
          </span>
        </div>

        {/* +/- Buttons */}
        <div className="relative z-10 flex items-center gap-5">
          <button
            onClick={decrement}
            disabled={count <= MIN}
            aria-label="Decrement"
            className="group flex h-16 w-16 items-center justify-center rounded-2xl border border-border bg-secondary text-foreground text-3xl font-bold transition-all duration-150 hover:border-primary hover:bg-primary hover:text-primary-foreground active:scale-95 disabled:cursor-not-allowed disabled:opacity-30"
          >
            −
          </button>

          <button
            onClick={increment}
            disabled={count >= MAX}
            aria-label="Increment"
            className="group flex h-16 w-16 items-center justify-center rounded-2xl border border-border bg-secondary text-foreground text-3xl font-bold transition-all duration-150 hover:border-primary hover:bg-primary hover:text-primary-foreground active:scale-95 disabled:cursor-not-allowed disabled:opacity-30"
          >
            +
          </button>
        </div>
      </div>

      {/* Reset */}
      <button
        onClick={reset}
        disabled={count === 0}
        className="text-sm tracking-widest uppercase text-muted-foreground transition-colors duration-150 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-30"
      >
        Reset
      </button>
    </div>
  );
};

export default Counter;
