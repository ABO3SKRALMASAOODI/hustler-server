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
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="flex flex-col items-center gap-8 rounded-2xl border border-border bg-card px-10 py-12 shadow-2xl w-full max-w-sm">

        {/* Title */}
        <h1
          className="text-sm font-semibold uppercase tracking-[0.2em] text-muted-foreground"
          style={{ fontFamily: "'DM Sans', sans-serif" }}
        >
          Counter
        </h1>

        {/* Count Display */}
        <div
          className={`text-[6rem] font-bold leading-none tabular-nums transition-colors duration-300 ${countColor}`}
          style={{ fontFamily: "'JetBrains Mono', monospace" }}
        >
          {count}
        </div>

        {/* +/- Buttons */}
        <div className="flex items-center gap-4 w-full">
          <button
            onClick={decrement}
            aria-label="Decrement"
            className="flex-1 rounded-xl border border-border bg-secondary py-5 text-3xl font-bold text-secondary-foreground transition-all duration-150 hover:border-destructive hover:text-destructive active:scale-95"
            style={{ fontFamily: "'JetBrains Mono', monospace" }}
          >
            −
          </button>

          <button
            onClick={increment}
            aria-label="Increment"
            className="flex-1 rounded-xl bg-primary py-5 text-3xl font-bold text-primary-foreground shadow-lg transition-all duration-150 hover:brightness-110 active:scale-95"
            style={{ fontFamily: "'JetBrains Mono', monospace" }}
          >
            +
          </button>
        </div>

        {/* Reset */}
        <button
          onClick={reset}
          aria-label="Reset counter"
          className="w-full rounded-xl border border-border bg-transparent py-3 text-sm font-medium text-muted-foreground transition-all duration-150 hover:border-primary hover:text-primary active:scale-95"
        >
          Reset
        </button>
      </div>
    </div>
  );
};

export default Counter;
