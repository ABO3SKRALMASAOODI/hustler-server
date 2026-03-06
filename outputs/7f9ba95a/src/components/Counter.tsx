import { useState } from "react";

const STEP = 1;
const MIN = -999;
const MAX = 999;

export default function Counter() {
  const [count, setCount] = useState(0);
  const [animate, setAnimate] = useState<"up" | "down" | null>(null);

  const trigger = (dir: "up" | "down") => {
    setAnimate(dir);
    setTimeout(() => setAnimate(null), 180);
  };

  const increment = () => {
    if (count >= MAX) return;
    setCount((c) => c + STEP);
    trigger("up");
  };

  const decrement = () => {
    if (count <= MIN) return;
    setCount((c) => c - STEP);
    trigger("down");
  };

  const reset = () => {
    setCount(0);
    setAnimate(null);
  };

  const isPositive = count > 0;
  const isNegative = count < 0;

  const countColor = isPositive
    ? "text-[hsl(var(--counter-increment))]"
    : isNegative
    ? "text-[hsl(var(--counter-decrement))]"
    : "text-foreground";

  const scaleClass =
    animate === "up"
      ? "scale-110"
      : animate === "down"
      ? "scale-90"
      : "scale-100";

  return (
    <div className="flex flex-col items-center gap-10">
      {/* Title */}
      <div className="text-center">
        <h1 className="text-3xl font-bold tracking-widest uppercase text-muted-foreground/60 mb-1 select-none">
          Counter
        </h1>
        <div className="h-px w-16 mx-auto bg-[hsl(var(--border))]" />
      </div>

      {/* Card */}
      <div className="relative flex flex-col items-center justify-center rounded-3xl border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-16 py-12 shadow-[0_8px_60px_rgba(0,0,0,0.5)]">
        {/* Count display */}
        <div
          className={`transition-transform duration-150 ease-in-out ${scaleClass}`}
        >
          <span
            className={`font-mono text-9xl font-black leading-none tracking-tighter tabular-nums select-none transition-colors duration-200 ${countColor}`}
          >
            {count >= 0 && count !== 0 ? `+${count}` : count}
          </span>
        </div>

        {/* Progress bar */}
        <div className="mt-8 h-1.5 w-64 rounded-full bg-[hsl(var(--secondary))] overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-300 ease-out"
            style={{
              width: `${Math.abs((count / MAX) * 100)}%`,
              marginLeft: count < 0 ? "auto" : undefined,
              background: isNegative
                ? "hsl(var(--counter-decrement))"
                : "hsl(var(--counter-increment))",
            }}
          />
        </div>

        <p className="mt-2 text-xs text-muted-foreground tracking-widest select-none">
          {MIN} — {MAX}
        </p>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-4">
        {/* Decrement */}
        <button
          onClick={decrement}
          disabled={count <= MIN}
          aria-label="Decrement"
          className="group flex h-16 w-16 items-center justify-center rounded-2xl border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--counter-decrement))] shadow-lg transition-all duration-150 hover:bg-[hsl(var(--counter-decrement))] hover:text-white hover:scale-105 active:scale-95 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:scale-100 disabled:hover:bg-[hsl(var(--card))] disabled:hover:text-[hsl(var(--counter-decrement))]"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="currentColor"
            className="h-7 w-7"
          >
            <path
              fillRule="evenodd"
              d="M4.25 12a.75.75 0 0 1 .75-.75h14a.75.75 0 0 1 0 1.5H5a.75.75 0 0 1-.75-.75Z"
              clipRule="evenodd"
            />
          </svg>
        </button>

        {/* Reset */}
        <button
          onClick={reset}
          aria-label="Reset"
          className="flex h-12 w-12 items-center justify-center rounded-xl border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-muted-foreground shadow transition-all duration-150 hover:bg-[hsl(var(--secondary))] hover:text-foreground hover:scale-105 active:scale-95"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="currentColor"
            className="h-5 w-5"
          >
            <path
              fillRule="evenodd"
              d="M4.755 10.059a7.5 7.5 0 0 1 12.548-3.364l1.903 1.903h-3.183a.75.75 0 1 0 0 1.5h4.992a.75.75 0 0 0 .75-.75V4.356a.75.75 0 0 0-1.5 0v3.18l-1.9-1.9A9 9 0 0 0 3.306 9.67a.75.75 0 1 0 1.45.388Zm15.408 3.352a.75.75 0 0 0-.919.53 7.5 7.5 0 0 1-12.548 3.364l-1.902-1.903h3.183a.75.75 0 0 0 0-1.5H2.984a.75.75 0 0 0-.75.75v4.992a.75.75 0 0 0 1.5 0v-3.18l1.9 1.9a9 9 0 0 0 15.059-4.035.75.75 0 0 0-.53-.918Z"
              clipRule="evenodd"
            />
          </svg>
        </button>

        {/* Increment */}
        <button
          onClick={increment}
          disabled={count >= MAX}
          aria-label="Increment"
          className="group flex h-16 w-16 items-center justify-center rounded-2xl border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--counter-increment))] shadow-lg transition-all duration-150 hover:bg-[hsl(var(--counter-increment))] hover:text-white hover:scale-105 active:scale-95 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:scale-100 disabled:hover:bg-[hsl(var(--card))] disabled:hover:text-[hsl(var(--counter-increment))]"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="currentColor"
            className="h-7 w-7"
          >
            <path
              fillRule="evenodd"
              d="M12 3.75a.75.75 0 0 1 .75.75v6.75h6.75a.75.75 0 0 1 0 1.5h-6.75v6.75a.75.75 0 0 1-1.5 0v-6.75H4.5a.75.75 0 0 1 0-1.5h6.75V4.5a.75.75 0 0 1 .75-.75Z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      </div>

      {/* Keyboard hint */}
      <p className="text-xs text-muted-foreground/50 tracking-widest select-none">
        − &nbsp; RESET &nbsp; +
      </p>
    </div>
  );
}
