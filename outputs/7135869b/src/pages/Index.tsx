import { useState } from "react";

const Index = () => {
  const [count, setCount] = useState(0);

  const increment = () => setCount((c) => c + 1);
  const decrement = () => setCount((c) => c - 1);
  const reset = () => setCount(0);

  const countColor =
    count > 0
      ? "text-blue-400"
      : count < 0
      ? "text-rose-400"
      : "text-foreground";

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-zinc-950 gap-10">
      <h1 className="text-zinc-500 text-sm uppercase tracking-[0.3em] font-medium">
        Counter
      </h1>

      <span
        className={`font-mono text-[9rem] leading-none font-bold tabular-nums transition-colors duration-300 ${countColor}`}
      >
        {count}
      </span>

      <div className="flex items-center gap-4">
        <button
          onClick={decrement}
          className="w-14 h-14 rounded-full bg-zinc-800 text-zinc-200 text-2xl font-bold hover:bg-zinc-700 active:scale-95 transition-all duration-150 flex items-center justify-center"
          aria-label="Decrement"
        >
          −
        </button>

        <button
          onClick={reset}
          className="px-6 h-10 rounded-full bg-zinc-800 text-zinc-400 text-xs uppercase tracking-widest hover:bg-zinc-700 hover:text-zinc-200 active:scale-95 transition-all duration-150"
          aria-label="Reset"
        >
          Reset
        </button>

        <button
          onClick={increment}
          className="w-14 h-14 rounded-full bg-blue-500 text-white text-2xl font-bold hover:bg-blue-400 active:scale-95 transition-all duration-150 flex items-center justify-center"
          aria-label="Increment"
        >
          +
        </button>
      </div>
    </div>
  );
};

export default Index;
