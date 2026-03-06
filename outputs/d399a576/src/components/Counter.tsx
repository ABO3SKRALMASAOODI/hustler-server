import { useState, useCallback } from "react";

const Counter = () => {
  const [count, setCount] = useState(0);
  const [animating, setAnimating] = useState<"up" | "down" | null>(null);

  const triggerAnimation = useCallback((dir: "up" | "down") => {
    setAnimating(dir);
    setTimeout(() => setAnimating(null), 150);
  }, []);

  const increment = () => {
    setCount((c) => c + 1);
    triggerAnimation("up");
  };

  const decrement = () => {
    setCount((c) => c - 1);
    triggerAnimation("down");
  };

  const reset = () => {
    setCount(0);
    setAnimating(null);
  };

  const isPositive = count > 0;
  const isNegative = count < 0;

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      {/* Title */}
      <p className="text-muted-foreground text-sm font-medium tracking-[0.25em] uppercase mb-12 select-none">
        Counter
      </p>

      {/* Card */}
      <div className="bg-card border border-border rounded-2xl shadow-2xl px-16 py-14 flex flex-col items-center gap-10 w-full max-w-sm">
        {/* Count Display */}
        <div
          className="relative flex items-center justify-center"
          style={{ minHeight: "7rem" }}
        >
          <span
            className="font-mono font-bold leading-none select-none transition-all duration-150"
            style={{
              fontSize: "clamp(4rem, 16vw, 7rem)",
              color: isPositive
                ? "hsl(var(--primary))"
                : isNegative
                ? "hsl(var(--destructive))"
                : "hsl(var(--foreground))",
              transform:
                animating === "up"
                  ? "scale(1.12) translateY(-4px)"
                  : animating === "down"
                  ? "scale(1.12) translateY(4px)"
                  : "scale(1) translateY(0)",
              display: "inline-block",
            }}
          >
            {count}
          </span>
        </div>

        {/* Main Buttons */}
        <div className="flex items-center gap-5 w-full">
          {/* Decrement */}
          <button
            onClick={decrement}
            aria-label="Decrement"
            className="flex-1 h-16 rounded-xl border border-border bg-secondary text-foreground text-3xl font-light
              transition-all duration-100 active:scale-95 hover:border-destructive hover:text-destructive
              focus:outline-none focus-visible:ring-2 focus-visible:ring-ring select-none"
          >
            −
          </button>

          {/* Increment */}
          <button
            onClick={increment}
            aria-label="Increment"
            className="flex-1 h-16 rounded-xl border border-border bg-secondary text-foreground text-3xl font-light
              transition-all duration-100 active:scale-95 hover:border-primary hover:text-primary
              focus:outline-none focus-visible:ring-2 focus-visible:ring-ring select-none"
          >
            +
          </button>
        </div>

        {/* Reset */}
        <button
          onClick={reset}
          aria-label="Reset counter"
          className="w-full h-11 rounded-xl bg-muted text-muted-foreground text-sm font-medium tracking-widest uppercase
            transition-all duration-100 active:scale-95 hover:bg-secondary hover:text-foreground
            focus:outline-none focus-visible:ring-2 focus-visible:ring-ring select-none"
        >
          Reset
        </button>
      </div>

      {/* Step hint */}
      <p className="mt-8 text-muted-foreground text-xs tracking-widest uppercase select-none">
        Step&nbsp;&nbsp;1
      </p>
    </div>
  );
};

export default Counter;
