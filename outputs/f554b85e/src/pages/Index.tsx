import { useState } from "react";

const Index = () => {
  const [count, setCount] = useState(0);

  const increment = () => setCount((c) => c + 1);
  const decrement = () => setCount((c) => c - 1);
  const reset = () => setCount(0);

  const isPositive = count > 0;
  const isNegative = count < 0;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-10">
        <h1 className="text-sm font-semibold uppercase tracking-[0.3em] text-muted-foreground">
          Counter
        </h1>

        <div
          className={`text-[8rem] font-black leading-none tabular-nums transition-colors duration-300 ${
            isPositive
              ? "text-emerald-500"
              : isNegative
              ? "text-rose-500"
              : "text-foreground"
          }`}
        >
          {count}
        </div>

        <div className="flex items-center gap-4">
          <button
            onClick={decrement}
            className="h-14 w-14 rounded-full border-2 border-border bg-background text-2xl font-bold text-foreground transition-all duration-150 hover:border-rose-400 hover:bg-rose-50 hover:text-rose-500 active:scale-95"
            aria-label="Decrement"
          >
            −
          </button>

          <button
            onClick={reset}
            className="h-10 rounded-full border border-border bg-background px-5 text-xs font-semibold uppercase tracking-widest text-muted-foreground transition-all duration-150 hover:border-foreground hover:text-foreground active:scale-95"
            aria-label="Reset"
          >
            Reset
          </button>

          <button
            onClick={increment}
            className="h-14 w-14 rounded-full border-2 border-border bg-background text-2xl font-bold text-foreground transition-all duration-150 hover:border-emerald-400 hover:bg-emerald-50 hover:text-emerald-500 active:scale-95"
            aria-label="Increment"
          >
            +
          </button>
        </div>
      </div>
    </div>
  );
};

export default Index;
