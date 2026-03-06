import { useState } from "react";

const Counter = () => {
  const [count, setCount] = useState(0);
  const [animating, setAnimating] = useState<"up" | "down" | null>(null);

  const triggerAnimation = (direction: "up" | "down") => {
    setAnimating(direction);
    setTimeout(() => setAnimating(null), 150);
  };

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

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      {/* Title */}
      <p className="text-muted-foreground text-sm uppercase tracking-[0.3em] font-medium mb-12">
        Counter
      </p>

      {/* Card */}
      <div className="relative flex flex-col items-center gap-10 bg-card border border-border rounded-2xl px-12 py-14 shadow-2xl w-full max-w-sm">
        {/* Red glow behind number */}
        <div
          className="absolute inset-0 rounded-2xl pointer-events-none"
          style={{
            background:
              "radial-gradient(ellipse at 50% 40%, hsl(0 85% 50% / 0.1) 0%, transparent 70%)",
          }}
        />

        {/* Count Display */}
        <div
          className="relative select-none"
          style={{
            transform:
              animating === "up"
                ? "translateY(-6px) scale(1.08)"
                : animating === "down"
                ? "translateY(6px) scale(1.08)"
                : "translateY(0px) scale(1)",
            transition: "transform 0.15s cubic-bezier(0.34, 1.56, 0.64, 1)",
          }}
        >
          <span
            className="font-mono font-bold leading-none"
            style={{
              fontSize: "clamp(5rem, 20vw, 8rem)",
              color: "hsl(0 85% 50%)",
              textShadow: "0 0 40px hsl(0 85% 50% / 0.5)",
            }}
          >
            {count > 0 ? `+${count}` : count}
          </span>
        </div>

        {/* Main Buttons */}
        <div className="flex items-center gap-4 w-full">
          <button
            onClick={decrement}
            aria-label="Decrement"
            className="flex-1 h-14 rounded-xl border border-border bg-secondary text-foreground text-3xl font-light
              hover:bg-primary hover:text-primary-foreground hover:border-primary
              active:scale-95 transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            −
          </button>

          <button
            onClick={increment}
            aria-label="Increment"
            className="flex-1 h-14 rounded-xl bg-primary text-primary-foreground text-3xl font-light
              hover:brightness-110
              active:scale-95 transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            style={{ boxShadow: "0 0 24px hsl(0 85% 50% / 0.4)" }}
          >
            +
          </button>
        </div>

        {/* Reset Button */}
        <button
          onClick={reset}
          aria-label="Reset"
          className="text-muted-foreground text-xs uppercase tracking-widest hover:text-foreground transition-colors duration-150 focus:outline-none focus-visible:underline"
        >
          Reset
        </button>
      </div>

      {/* Step info */}
      <p className="mt-8 text-muted-foreground text-xs tracking-widest uppercase">
        Step · 1
      </p>
    </div>
  );
};

export default Counter;
