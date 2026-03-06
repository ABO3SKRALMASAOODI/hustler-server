import { useState } from "react";

const Counter = () => {
  const [count, setCount] = useState(0);

  const increment = () => setCount((c) => c + 1);
  const decrement = () => setCount((c) => c - 1);
  const reset = () => setCount(0);

  const countColor =
    count > 0
      ? "text-primary"
      : count < 0
      ? "text-destructive"
      : "text-foreground";

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      {/* Card */}
      <div className="flex w-full max-w-sm flex-col items-center gap-10 rounded-2xl border border-border bg-card px-10 py-14 shadow-xl">
        {/* Label */}
        <p className="text-xs font-semibold uppercase tracking-[0.25em] text-muted-foreground">
          Counter
        </p>

        {/* Count Display */}
        <div
          className={`select-none font-mono text-8xl font-bold tabular-nums transition-colors duration-200 ${countColor}`}
          aria-live="polite"
          aria-label={`Current count: ${count}`}
        >
          {count}
        </div>

        {/* Controls */}
        <div className="flex w-full items-center justify-between gap-4">
          {/* Decrement */}
          <button
            onClick={decrement}
            aria-label="Decrement"
            className="flex h-14 w-14 items-center justify-center rounded-full border border-border bg-secondary text-2xl font-bold text-foreground shadow-sm transition-all duration-150 hover:scale-105 hover:bg-muted active:scale-95"
          >
            −
          </button>

          {/* Reset */}
          <button
            onClick={reset}
            aria-label="Reset"
            className="flex h-10 items-center justify-center rounded-full border border-border bg-background px-5 text-xs font-semibold uppercase tracking-widest text-muted-foreground shadow-sm transition-all duration-150 hover:border-primary hover:text-primary active:scale-95"
          >
            Reset
          </button>

          {/* Increment */}
          <button
            onClick={increment}
            aria-label="Increment"
            className="flex h-14 w-14 items-center justify-center rounded-full bg-primary text-2xl font-bold text-primary-foreground shadow-md transition-all duration-150 hover:scale-105 hover:brightness-110 active:scale-95"
          >
            +
          </button>
        </div>
      </div>

      {/* Footer hint */}
      <p className="mt-8 text-xs text-muted-foreground">
        Tap <span className="font-semibold text-primary">+</span> or{" "}
        <span className="font-semibold">−</span> to count
      </p>
    </div>
  );
};

export default Counter;
