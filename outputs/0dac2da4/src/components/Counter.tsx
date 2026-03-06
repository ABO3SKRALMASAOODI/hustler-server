import { useState, useCallback } from "react";

const MIN = -999;
const MAX = 999;

const Counter = () => {
  const [count, setCount] = useState(0);
  const [bump, setBump] = useState<"up" | "down" | null>(null);

  const triggerBump = (dir: "up" | "down") => {
    setBump(dir);
    setTimeout(() => setBump(null), 200);
  };

  const increment = useCallback(() => {
    if (count >= MAX) return;
    setCount((c) => c + 1);
    triggerBump("up");
  }, [count]);

  const decrement = useCallback(() => {
    if (count <= MIN) return;
    setCount((c) => c - 1);
    triggerBump("down");
  }, [count]);

  const reset = useCallback(() => {
    setCount(0);
    setBump(null);
  }, []);

  const isPositive = count > 0;
  const isNegative = count < 0;
  const isZero = count === 0;

  const numberColor = isPositive
    ? "text-[hsl(185,96%,54%)]"
    : isNegative
    ? "text-[hsl(0,72%,68%)]"
    : "text-foreground";

  const bumpClass =
    bump === "up"
      ? "translate-y-[-4px]"
      : bump === "down"
      ? "translate-y-[4px]"
      : "translate-y-0";

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-10">

        {/* Title */}
        <div className="text-center">
          <p
            className="font-mono text-xs uppercase tracking-[0.3em] text-muted-foreground"
            style={{ fontFamily: "'Space Mono', monospace" }}
          >
            Counter
          </p>
        </div>

        {/* Card */}
        <div
          className="relative flex flex-col items-center justify-center rounded-2xl border border-border bg-card px-16 py-12 shadow-2xl"
          style={{ boxShadow: "0 0 60px hsl(185 96% 54% / 0.07), 0 20px 60px hsl(0 0% 0% / 0.4)" }}
        >
          {/* Decorative glow */}
          <div
            className="pointer-events-none absolute inset-0 rounded-2xl"
            style={{
              background:
                "radial-gradient(ellipse at 50% 0%, hsl(185 96% 54% / 0.08) 0%, transparent 70%)",
            }}
          />

          {/* Count display */}
          <div
            className={`transition-transform duration-200 ease-out ${bumpClass}`}
          >
            <span
              className={`select-none font-mono text-[7rem] font-bold leading-none tracking-tighter transition-colors duration-300 md:text-[9rem] ${numberColor}`}
              style={{ fontFamily: "'Space Mono', monospace" }}
            >
              {count > 0 ? `+${count}` : count}
            </span>
          </div>

          {/* Step indicator */}
          <div className="mt-4 flex items-center gap-2">
            <div
              className={`h-1.5 w-1.5 rounded-full transition-colors duration-300 ${
                isZero ? "bg-muted-foreground" : isPositive ? "bg-[hsl(185,96%,54%)]" : "bg-[hsl(0,72%,68%)]"
              }`}
            />
            <span
              className="font-mono text-xs text-muted-foreground"
              style={{ fontFamily: "'Space Mono', monospace" }}
            >
              {isZero ? "neutral" : isPositive ? "positive" : "negative"}
            </span>
          </div>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-4">
          {/* Decrement */}
          <button
            onClick={decrement}
            disabled={count <= MIN}
            aria-label="Decrement"
            className="group flex h-16 w-16 items-center justify-center rounded-xl border border-border bg-card text-foreground shadow-lg transition-all duration-150 hover:border-[hsl(0,72%,68%)] hover:bg-[hsl(0,72%,68%)/0.1] hover:text-[hsl(0,72%,68%)] active:scale-95 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
            >
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>

          {/* Reset */}
          <button
            onClick={reset}
            aria-label="Reset"
            className="flex h-10 items-center justify-center rounded-lg border border-border bg-card px-5 font-mono text-xs uppercase tracking-widest text-muted-foreground shadow transition-all duration-150 hover:border-ring hover:text-foreground active:scale-95"
            style={{ fontFamily: "'Space Mono', monospace" }}
          >
            Reset
          </button>

          {/* Increment */}
          <button
            onClick={increment}
            disabled={count >= MAX}
            aria-label="Increment"
            className="group flex h-16 w-16 items-center justify-center rounded-xl border border-border bg-card text-foreground shadow-lg transition-all duration-150 hover:border-[hsl(185,96%,54%)] hover:bg-[hsl(185,96%,54%)/0.1] hover:text-[hsl(185,96%,54%)] active:scale-95 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
            >
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
        </div>

        {/* Keyboard hint */}
        <p
          className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground opacity-50"
          style={{ fontFamily: "'Space Mono', monospace" }}
        >
          range: {MIN} — {MAX}
        </p>
      </div>
    </div>
  );
};

export default Counter;
