import { useState } from "react";

const Counter = () => {
  const [count, setCount] = useState(0);

  const increment = () => setCount((c) => c + 1);
  const decrement = () => setCount((c) => c - 1);
  const reset = () => setCount(0);

  const isPositive = count > 0;
  const isNegative = count < 0;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="flex flex-col items-center gap-10">
        {/* Title */}
        <div className="text-center">
          <h1 className="text-sm font-semibold uppercase tracking-[0.3em] text-muted-foreground">
            Counter
          </h1>
        </div>

        {/* Count Display */}
        <div className="relative flex h-56 w-56 items-center justify-center rounded-full border-4 border-card bg-card shadow-2xl">
          <span
            className={[
              "text-7xl font-black tabular-nums transition-colors duration-300",
              isPositive
                ? "text-primary"
                : isNegative
                ? "text-destructive"
                : "text-foreground",
            ].join(" ")}
          >
            {count}
          </span>
          {/* Subtle ring glow */}
          <div
            className={[
              "absolute inset-0 rounded-full opacity-10 transition-colors duration-300",
              isPositive
                ? "bg-primary"
                : isNegative
                ? "bg-destructive"
                : "bg-transparent",
            ].join(" ")}
          />
        </div>

        {/* Main Controls */}
        <div className="flex items-center gap-5">
          {/* Decrement */}
          <button
            onClick={decrement}
            aria-label="Decrement"
            className="group flex h-16 w-16 items-center justify-center rounded-full border-2 border-destructive bg-card text-destructive shadow-md transition-all duration-150 hover:bg-destructive hover:text-destructive-foreground active:scale-95"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={3}
              strokeLinecap="round"
              className="h-6 w-6"
            >
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>

          {/* Increment */}
          <button
            onClick={increment}
            aria-label="Increment"
            className="group flex h-20 w-20 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg shadow-primary/40 transition-all duration-150 hover:brightness-110 active:scale-95"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={3}
              strokeLinecap="round"
              className="h-7 w-7"
            >
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>

          {/* Decrement (spacer mirror — empty div for symmetry) */}
          <div className="h-16 w-16" />
        </div>

        {/* Reset */}
        <button
          onClick={reset}
          aria-label="Reset counter"
          className="rounded-full px-8 py-2 text-xs font-semibold uppercase tracking-widest text-muted-foreground transition-all duration-150 hover:bg-secondary hover:text-foreground active:scale-95"
        >
          Reset
        </button>
      </div>
    </div>
  );
};

export default Counter;
