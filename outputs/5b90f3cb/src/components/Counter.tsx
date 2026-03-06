import { useState } from "react";

const Counter = () => {
  const [count, setCount] = useState(0);

  const increment = () => setCount((c) => c + 1);
  const decrement = () => setCount((c) => c - 1);
  const reset = () => setCount(0);

  const isPositive = count > 0;
  const isNegative = count < 0;

  return (
    <div className="flex flex-col items-center justify-center gap-10">
      {/* Title */}
      <h1 className="text-sm font-mono uppercase tracking-[0.4em] text-muted-foreground">
        Counter
      </h1>

      {/* Count display */}
      <div className="relative flex items-center justify-center">
        <div
          className="absolute inset-0 rounded-full blur-3xl opacity-20"
          style={{
            background: isPositive
              ? "hsl(18 92% 58%)"
              : isNegative
              ? "hsl(220 60% 60%)"
              : "hsl(40 20% 50%)",
          }}
        />
        <span
          className="relative font-mono font-black leading-none select-none transition-all duration-300"
          style={{
            fontSize: "clamp(5rem, 20vw, 12rem)",
            color: isPositive
              ? "hsl(var(--primary))"
              : isNegative
              ? "hsl(220 80% 70%)"
              : "hsl(var(--foreground))",
          }}
        >
          {count > 0 ? `+${count}` : count}
        </span>
      </div>

      {/* Buttons */}
      <div className="flex items-center gap-4">
        <button
          onClick={decrement}
          aria-label="Decrement"
          className="group flex h-16 w-16 items-center justify-center rounded-2xl border border-border bg-card text-foreground text-3xl font-mono font-bold transition-all duration-150 hover:border-primary hover:text-primary hover:scale-110 active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          −
        </button>

        <button
          onClick={reset}
          aria-label="Reset"
          className="flex h-10 items-center justify-center rounded-xl border border-border bg-card px-5 text-xs font-mono uppercase tracking-widest text-muted-foreground transition-all duration-150 hover:border-foreground hover:text-foreground active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          Reset
        </button>

        <button
          onClick={increment}
          aria-label="Increment"
          className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary text-primary-foreground text-3xl font-mono font-bold transition-all duration-150 hover:opacity-90 hover:scale-110 active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          +
        </button>
      </div>

      {/* Step indicator */}
      <p className="text-xs font-mono text-muted-foreground tracking-widest">
        {count === 0
          ? "Start counting"
          : `${Math.abs(count)} step${Math.abs(count) !== 1 ? "s" : ""} ${isPositive ? "up" : "down"}`}
      </p>
    </div>
  );
};

export default Counter;
