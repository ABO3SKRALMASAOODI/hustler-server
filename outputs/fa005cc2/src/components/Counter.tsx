import { useState } from "react";

const MIN = -999;
const MAX = 999;

const Counter = () => {
  const [count, setCount] = useState(0);

  const increment = () => setCount((c) => Math.min(c + 1, MAX));
  const decrement = () => setCount((c) => Math.max(c - 1, MIN));
  const reset = () => setCount(0);

  const isPositive = count > 0;
  const isNegative = count < 0;

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      {/* Card */}
      <div className="w-full max-w-sm rounded-2xl border border-border bg-card shadow-2xl p-10 flex flex-col items-center gap-8">

        {/* Title */}
        <p className="text-xs font-semibold uppercase tracking-[0.25em] text-muted-foreground">
          Counter
        </p>

        {/* Count Display */}
        <div
          className="relative flex items-center justify-center w-48 h-48 rounded-full border-4 select-none"
          style={{
            borderColor: isPositive
              ? "hsl(16 85% 55%)"
              : isNegative
              ? "hsl(217 80% 55%)"
              : "hsl(var(--border))",
            transition: "border-color 0.3s ease",
          }}
        >
          <span
            className="font-black tabular-nums leading-none"
            style={{
              fontSize: count <= -100 || count >= 100 ? "3.5rem" : "5rem",
              color: isPositive
                ? "hsl(16 85% 55%)"
                : isNegative
                ? "hsl(217 80% 55%)"
                : "hsl(var(--foreground))",
              transition: "color 0.3s ease, font-size 0.15s ease",
            }}
          >
            {count}
          </span>
        </div>

        {/* +/- Buttons */}
        <div className="flex items-center gap-5 w-full">
          {/* Decrement */}
          <button
            onClick={decrement}
            disabled={count <= MIN}
            aria-label="Decrement"
            className="flex-1 h-16 rounded-xl text-3xl font-bold transition-all duration-150
              active:scale-95 disabled:opacity-30 disabled:cursor-not-allowed"
            style={{
              background: "hsl(217 80% 55%)",
              color: "hsl(0 0% 100%)",
              boxShadow: "0 4px 0 hsl(217 80% 40%)",
            }}
            onMouseDown={(e) =>
              ((e.currentTarget as HTMLButtonElement).style.boxShadow =
                "0 1px 0 hsl(217 80% 40%)")
            }
            onMouseUp={(e) =>
              ((e.currentTarget as HTMLButtonElement).style.boxShadow =
                "0 4px 0 hsl(217 80% 40%)")
            }
            onMouseLeave={(e) =>
              ((e.currentTarget as HTMLButtonElement).style.boxShadow =
                "0 4px 0 hsl(217 80% 40%)")
            }
          >
            −
          </button>

          {/* Increment */}
          <button
            onClick={increment}
            disabled={count >= MAX}
            aria-label="Increment"
            className="flex-1 h-16 rounded-xl text-3xl font-bold transition-all duration-150
              active:scale-95 disabled:opacity-30 disabled:cursor-not-allowed"
            style={{
              background: "hsl(16 85% 55%)",
              color: "hsl(0 0% 100%)",
              boxShadow: "0 4px 0 hsl(16 85% 38%)",
            }}
            onMouseDown={(e) =>
              ((e.currentTarget as HTMLButtonElement).style.boxShadow =
                "0 1px 0 hsl(16 85% 38%)")
            }
            onMouseUp={(e) =>
              ((e.currentTarget as HTMLButtonElement).style.boxShadow =
                "0 4px 0 hsl(16 85% 38%)")
            }
            onMouseLeave={(e) =>
              ((e.currentTarget as HTMLButtonElement).style.boxShadow =
                "0 4px 0 hsl(16 85% 38%)")
            }
          >
            +
          </button>
        </div>

        {/* Reset */}
        <button
          onClick={reset}
          aria-label="Reset"
          className="w-full h-11 rounded-xl text-sm font-semibold uppercase tracking-widest
            bg-muted text-muted-foreground hover:bg-secondary transition-colors duration-150
            active:scale-95"
        >
          Reset
        </button>
      </div>

      {/* Range hint */}
      <p className="mt-6 text-xs text-muted-foreground tracking-wide">
        Range: {MIN} — {MAX}
      </p>
    </div>
  );
};

export default Counter;
