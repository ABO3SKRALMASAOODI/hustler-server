import { useState } from "react";

const Counter = () => {
  const [count, setCount] = useState(0);

  const increment = () => setCount((c) => c + 1);
  const decrement = () => setCount((c) => c - 1);
  const reset = () => setCount(0);

  const isPositive = count > 0;
  const isNegative = count < 0;

  return (
    <div className="flex flex-col items-center gap-10">
      <h1 className="text-sm font-mono uppercase tracking-[0.3em] text-muted-foreground">
        Counter
      </h1>

      <div
        className={`font-mono text-[8rem] leading-none font-bold tabular-nums transition-colors duration-300 ${
          isPositive
            ? "text-foreground"
            : isNegative
            ? "text-destructive"
            : "text-muted-foreground"
        }`}
      >
        {count}
      </div>

      <div className="flex items-center gap-4">
        <button
          onClick={decrement}
          aria-label="Decrement"
          className="w-14 h-14 rounded-full border border-border bg-background text-foreground text-2xl font-light hover:bg-secondary transition-colors duration-150 active:scale-95"
        >
          −
        </button>

        <button
          onClick={reset}
          aria-label="Reset"
          className="w-10 h-10 rounded-full border border-border bg-background text-muted-foreground text-xs font-mono uppercase tracking-widest hover:bg-secondary transition-colors duration-150 active:scale-95"
        >
          0
        </button>

        <button
          onClick={increment}
          aria-label="Increment"
          className="w-14 h-14 rounded-full border border-border bg-foreground text-primary-foreground text-2xl font-light hover:opacity-80 transition-opacity duration-150 active:scale-95"
        >
          +
        </button>
      </div>
    </div>
  );
};

export default Counter;
