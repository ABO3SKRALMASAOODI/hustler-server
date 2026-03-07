import { CountdownTimer } from "@/components/CountdownTimer";
import { EmailForm } from "@/components/EmailForm";
import { SocialLinks } from "@/components/SocialLinks";

// Animated dot grid background
function DotGrid() {
  return (
    <div
      className="fixed inset-0 pointer-events-none"
      style={{
        backgroundImage:
          "radial-gradient(circle, hsl(0 0% 100% / 0.06) 1px, transparent 1px)",
        backgroundSize: "40px 40px",
      }}
    />
  );
}

// Decorative red line accent
function RedLine({ className }: { className?: string }) {
  return <div className={`h-px bg-red ${className ?? ""}`} />;
}

// Feature pill
function FeaturePill({ text }: { text: string }) {
  return (
    <div className="inline-flex items-center gap-2 px-3 py-1.5 border border-border bg-surface">
      <div className="w-1 h-1 rounded-full bg-red" />
      <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
        {text}
      </span>
    </div>
  );
}

// Horizontal rule with red center accent
function DividerLine() {
  return (
    <div className="flex items-center gap-4 w-full">
      <div className="flex-1 h-px bg-border" />
      <div className="w-2 h-2 rotate-45 border border-red bg-red/20" />
      <div className="flex-1 h-px bg-border" />
    </div>
  );
}

const FEATURES = [
  "Early Access",
  "Exclusive Beta",
  "Priority Onboarding",
  "Founding Member Perks",
];

const STATS = [
  { value: "12K+", label: "Signups" },
  { value: "48H", label: "Avg Response" },
  { value: "100%", label: "Free to Join" },
];

export default function Index() {
  return (
    <div className="relative min-h-screen bg-background text-foreground overflow-x-hidden grain-overlay">
      {/* Dot grid */}
      <DotGrid />

      {/* Red ambient glow — top left */}
      <div
        className="fixed top-0 left-0 w-[600px] h-[600px] pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse at top left, hsl(350 84% 46% / 0.12) 0%, transparent 65%)",
        }}
      />

      {/* Red ambient glow — bottom right */}
      <div
        className="fixed bottom-0 right-0 w-[500px] h-[500px] pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse at bottom right, hsl(350 84% 46% / 0.08) 0%, transparent 65%)",
        }}
      />

      {/* ── HEADER ────────────────────────────────────────────── */}
      <header className="relative z-10 flex items-center justify-between px-6 sm:px-10 py-6 border-b border-border">
        <div className="flex items-center gap-3">
          <div className="w-6 h-6 bg-red clip-corner" />
          <span className="font-display text-xl tracking-wider text-foreground">
            NEXUS
          </span>
        </div>

        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-red animate-pulse" />
          <span className="font-mono text-[11px] text-muted-foreground uppercase tracking-widest">
            Coming Soon
          </span>
        </div>
      </header>

      {/* ── HERO ──────────────────────────────────────────────── */}
      <main className="relative z-10 flex flex-col items-center px-6 sm:px-10 pt-16 sm:pt-20 md:pt-24 pb-20">



        {/* Headline */}
        <div className="animate-fade-up-delay-1 text-center max-w-5xl">
          <h1 className="font-display text-[clamp(4rem,14vw,11rem)] leading-[0.88] tracking-wide text-foreground uppercase">
            The Future
            <br />
            <span className="text-red glow-red-text">Is Loading</span>
          </h1>
        </div>

        {/* Subheadline */}
        <p className="animate-fade-up-delay-2 mt-8 max-w-xl text-center font-sans text-base sm:text-lg text-muted-foreground leading-relaxed font-light">
          We're building something{" "}
          <span className="text-foreground font-medium">radically different</span>.
          A platform that rewires how you work, create, and connect.
          Get first access before we open the doors.
        </p>

        {/* Feature pills */}
        <div className="animate-fade-up-delay-2 flex flex-wrap justify-center gap-2 mt-6">
          {FEATURES.map((f) => (
            <FeaturePill key={f} text={f} />
          ))}
        </div>

        {/* Divider */}
        <div className="animate-fade-up-delay-3 w-full max-w-2xl mt-14 mb-12">
          <DividerLine />
        </div>

        {/* Countdown Timer */}
        <div className="animate-fade-up-delay-3 flex flex-col items-center gap-4 mb-12">
          <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-muted-foreground mb-2">
            Launch Countdown
          </p>
          <CountdownTimer />
        </div>

        {/* Divider */}
        <div className="animate-fade-up-delay-4 w-full max-w-2xl mb-12">
          <DividerLine />
        </div>

        {/* Email Form */}
        <div className="animate-fade-up-delay-4 flex flex-col items-center gap-4 w-full max-w-md">
          <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-muted-foreground">
            Secure Your Spot
          </p>
          <EmailForm />
        </div>

        {/* Stats Row */}
        <div className="animate-fade-up-delay-5 grid grid-cols-3 gap-8 sm:gap-16 mt-16 mb-16">
          {STATS.map((stat) => (
            <div key={stat.label} className="flex flex-col items-center gap-1">
              <span className="font-display text-3xl sm:text-4xl text-foreground tracking-wide">
                {stat.value}
              </span>
              <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                {stat.label}
              </span>
            </div>
          ))}
        </div>

        {/* Wide red line */}
        <div className="animate-fade-up-delay-5 w-full max-w-3xl mb-12">
          <RedLine />
        </div>

        {/* Bottom section: social + fine print */}
        <div className="animate-fade-up-delay-5 flex flex-col sm:flex-row items-center justify-between w-full max-w-3xl gap-6">
          <div className="flex flex-col gap-2">
            <p className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
              Follow the Journey
            </p>
            <SocialLinks />
          </div>

          <div className="text-center sm:text-right">
            <p className="font-mono text-[11px] text-muted-foreground leading-relaxed">
              Questions?{" "}
              <a
                href="mailto:hello@nexus.io"
                className="text-red hover:underline transition-colors"
              >
                hello@nexus.io
              </a>
            </p>
            <p className="font-mono text-[11px] text-muted-foreground/40 mt-1">
              © 2025 Nexus. All rights reserved.
            </p>
          </div>
        </div>
      </main>

      {/* ── DECORATIVE CORNER MARKS ────────────────────────────── */}
      <div className="fixed top-0 left-0 w-6 h-6 border-t border-l border-red/40 pointer-events-none z-20" />
      <div className="fixed top-0 right-0 w-6 h-6 border-t border-r border-red/40 pointer-events-none z-20" />
      <div className="fixed bottom-0 left-0 w-6 h-6 border-b border-l border-red/40 pointer-events-none z-20" />
      <div className="fixed bottom-0 right-0 w-6 h-6 border-b border-r border-red/40 pointer-events-none z-20" />

      {/* ── SCAN LINE EFFECT ────────────────────────────────────── */}
      <div
        className="fixed inset-0 pointer-events-none z-[9998] opacity-[0.02]"
        style={{
          background:
            "repeating-linear-gradient(0deg, transparent, transparent 2px, #000 2px, #000 4px)",
        }}
      />
    </div>
  );
}
