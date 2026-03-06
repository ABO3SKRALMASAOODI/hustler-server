import { useCountdown } from "@/hooks/useCountdown";

// Launch date: 90 days from a fixed reference — feels real, not obvious
const LAUNCH_DATE = new Date("2025-10-31T00:00:00Z");

interface TimerBlockProps {
  value: number;
  label: string;
}

function TimerBlock({ value, label }: TimerBlockProps) {
  const padded = String(value).padStart(2, "0");

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative">
        {/* Background plate */}
        <div className="w-20 h-20 sm:w-24 sm:h-24 md:w-28 md:h-28 bg-surface border border-border clip-corner flex items-center justify-center">
          <span className="font-mono text-3xl sm:text-4xl md:text-5xl font-bold text-foreground animate-flicker tabular-nums">
            {padded}
          </span>
        </div>
        {/* Red corner accent */}
        <div className="absolute top-0 right-0 w-2 h-2 bg-red" />
      </div>
      <span className="font-mono text-[10px] sm:text-xs uppercase tracking-[0.2em] text-muted-foreground">
        {label}
      </span>
    </div>
  );
}

export function CountdownTimer() {
  const { days, hours, minutes, seconds } = useCountdown(LAUNCH_DATE);

  return (
    <div className="flex items-center gap-3 sm:gap-5 md:gap-8">
      <TimerBlock value={days} label="Days" />
      <Separator />
      <TimerBlock value={hours} label="Hours" />
      <Separator />
      <TimerBlock value={minutes} label="Minutes" />
      <Separator />
      <TimerBlock value={seconds} label="Seconds" />
    </div>
  );
}

function Separator() {
  return (
    <div className="flex flex-col gap-2 mb-6">
      <div className="w-1 h-1 rounded-full bg-red" />
      <div className="w-1 h-1 rounded-full bg-red" />
    </div>
  );
}
