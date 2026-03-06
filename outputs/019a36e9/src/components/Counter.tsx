import { useState, useCallback } from "react";

const MIN = -999;
const MAX = 999;

const Counter = () => {
  const [count, setCount] = useState(0);
  const [animDir, setAnimDir] = useState<"up" | "down" | null>(null);

  const triggerAnim = (dir: "up" | "down") => {
    setAnimDir(null);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => setAnimDir(dir));
    });
  };

  const increment = useCallback(() => {
    if (count >= MAX) return;
    setCount((c) => c + 1);
    triggerAnim("up");
  }, [count]);

  const decrement = useCallback(() => {
    if (count <= MIN) return;
    setCount((c) => c - 1);
    triggerAnim("down");
  }, [count]);

  const reset = useCallback(() => {
    setCount(0);
    setAnimDir(null);
  }, []);

  const isPositive = count > 0;
  const isNegative = count < 0;
  const atMax = count >= MAX;
  const atMin = count <= MIN;

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      {/* Title */}
      <p className="text-muted-foreground text-sm tracking-[0.3em] uppercase font-medium mb-12 select-none">
        Counter
      </p>

      {/* Display Card */}
      <div
        className="relative flex items-center justify-center w-72 h-48 rounded-2xl border border-border bg-card shadow-2xl mb-12 overflow-hidden"
        style={{ boxShadow: "0 0 60px 0 hsla(38,95%,55%,0.08)" }}
      >
        {/* Subtle glow backing */}
        <div
          className="absolute inset-0 opacity-10 pointer-events-none"
          style={{
            background: isPositive
              ? "radial-gradient(ellipse at center, hsl(38,95%,55%) 0%, transparent 70%)"
              : isNegative
              ? "radial-gradient(ellipse at center, hsl(0,72%,51%) 0%, transparent 70%)"
              : "radial-gradient(ellipse at center, hsl(30,15%,55%) 0%, transparent 70%)",
            transition: "background 0.4s ease",
          }}
        />

        {/* Count number */}
        <span
          key={count}
          className="text-8xl font-mono font-bold select-none"
          style={{
            color: isPositive
              ? "hsl(38,95%,55%)"
              : isNegative
              ? "hsl(0,72%,61%)"
              : "hsl(40,30%,92%)",
            transition: "color 0.3s ease",
            letterSpacing: "-0.04em",
            animation: animDir === "up"
              ? "slideUp 0.18s cubic-bezier(0.22,1,0.36,1)"
              : animDir === "down"
              ? "slideDown 0.18s cubic-bezier(0.22,1,0.36,1)"
              : "none",
          }}
        >
          {count > 0 ? `+${count}` : count}
        </span>
      </div>

      {/* Main Controls */}
      <div className="flex items-center gap-5 mb-6">
        {/* Decrement */}
        <button
          onClick={decrement}
          disabled={atMin}
          aria-label="Decrement"
          className="group flex items-center justify-center w-16 h-16 rounded-2xl border border-border bg-card text-foreground text-3xl font-light transition-all duration-150 active:scale-90 hover:border-primary hover:text-primary disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:border-border disabled:hover:text-foreground select-none"
          style={{ userSelect: "none" }}
        >
          <span className="leading-none mb-0.5">−</span>
        </button>

        {/* Increment */}
        <button
          onClick={increment}
          disabled={atMax}
          aria-label="Increment"
          className="group flex items-center justify-center w-16 h-16 rounded-2xl border border-primary bg-primary text-primary-foreground text-3xl font-light transition-all duration-150 active:scale-90 hover:brightness-110 disabled:opacity-30 disabled:cursor-not-allowed select-none"
          style={{ userSelect: "none" }}
        >
          <span className="leading-none mb-0.5">+</span>
        </button>
      </div>

      {/* Reset */}
      <button
        onClick={reset}
        aria-label="Reset"
        className="text-muted-foreground text-xs tracking-widest uppercase hover:text-foreground transition-colors duration-200 select-none px-4 py-2 rounded-lg hover:bg-secondary"
      >
        Reset
      </button>

      {/* Range hint */}
      <p className="mt-10 text-muted-foreground text-xs tracking-wider select-none">
        Range: {MIN} — {MAX}
      </p>

      <style>{`
        @keyframes slideUp {
          from { opacity: 0.4; transform: translateY(10px) scale(0.97); }
          to   { opacity: 1;   transform: translateY(0)    scale(1); }
        }
        @keyframes slideDown {
          from { opacity: 0.4; transform: translateY(-10px) scale(0.97); }
          to   { opacity: 1;   transform: translateY(0)     scale(1); }
        }
      `}</style>
    </div>
  );
};

export default Counter;
