import { useState } from "react";
import { CheckCircle, ArrowRight, Loader2 } from "lucide-react";

interface FormState {
  status: "idle" | "loading" | "success" | "error";
  message: string;
}

export function EmailForm() {
  const [email, setEmail] = useState("");
  const [form, setForm] = useState<FormState>({ status: "idle", message: "" });

  const isValidEmail = (val: string) =>
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    if (!isValidEmail(email)) {
      setForm({ status: "error", message: "Enter a valid email address." });
      return;
    }

    setForm({ status: "loading", message: "" });

    // Simulate API call
    setTimeout(() => {
      setForm({
        status: "success",
        message: "You're on the list. We'll be in touch.",
      });
      setEmail("");
    }, 1200);
  }

  if (form.status === "success") {
    return (
      <div className="flex items-center gap-3 px-5 py-4 bg-surface border border-red/40 clip-corner animate-fade-up">
        <CheckCircle className="w-5 h-5 text-red shrink-0" />
        <p className="font-mono text-sm text-foreground">{form.message}</p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="w-full max-w-md">
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <input
            type="email"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              if (form.status === "error") setForm({ status: "idle", message: "" });
            }}
            placeholder="your@email.com"
            className="w-full h-12 px-4 bg-surface border border-border text-foreground placeholder:text-muted-foreground font-mono text-sm outline-none focus:border-red transition-colors duration-200 clip-corner"
            disabled={form.status === "loading"}
          />
          {/* Animated focus line */}
          <div className="absolute bottom-0 left-0 h-px w-0 bg-red transition-all duration-300 peer-focus:w-full" />
        </div>

        <button
          type="submit"
          disabled={form.status === "loading" || !email}
          className="h-12 px-6 bg-red text-primary-foreground font-display text-lg tracking-wider uppercase hover:brightness-110 active:scale-95 transition-all duration-150 animate-pulse-glow disabled:opacity-50 disabled:cursor-not-allowed clip-corner flex items-center gap-2 shrink-0"
        >
          {form.status === "loading" ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Joining…</span>
            </>
          ) : (
            <>
              <span>Join Waitlist</span>
              <ArrowRight className="w-4 h-4" />
            </>
          )}
        </button>
      </div>

      {form.status === "error" && (
        <p className="mt-2 font-mono text-xs text-red">{form.message}</p>
      )}

      <p className="mt-3 font-mono text-[11px] text-muted-foreground">
        No spam. Early access guaranteed.{" "}
        <span className="text-foreground/40">·</span> Unsubscribe anytime.
      </p>
    </form>
  );
}
