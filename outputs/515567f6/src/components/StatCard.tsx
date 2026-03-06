import { TrendingUp, TrendingDown, DollarSign, Users, Clock } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { StatCardData } from '@/utils/dashboardData';

interface StatCardProps {
  data: StatCardData;
  index: number;
}

const iconComponents: Record<string, React.ReactNode> = {
  DollarSign: <DollarSign size={20} />,
  Users: <Users size={20} />,
  TrendingUp: <TrendingUp size={20} />,
  Clock: <Clock size={20} />,
};

const colorConfig: Record<string, { icon: string; glow: string; badge: string }> = {
  red: {
    icon: 'text-[hsl(var(--dash-red))] bg-[hsl(var(--dash-red)/0.12)]',
    glow: 'hover:shadow-[0_0_24px_hsl(var(--dash-red)/0.18)]',
    badge: 'text-[hsl(var(--dash-red))] bg-[hsl(var(--dash-red)/0.1)]',
  },
  blue: {
    icon: 'text-[hsl(var(--dash-blue))] bg-[hsl(var(--dash-blue)/0.12)]',
    glow: 'hover:shadow-[0_0_24px_hsl(var(--dash-blue)/0.18)]',
    badge: 'text-[hsl(var(--dash-blue))] bg-[hsl(var(--dash-blue)/0.1)]',
  },
  green: {
    icon: 'text-[hsl(var(--dash-green))] bg-[hsl(var(--dash-green)/0.12)]',
    glow: 'hover:shadow-[0_0_24px_hsl(var(--dash-green)/0.18)]',
    badge: 'text-[hsl(var(--dash-green))] bg-[hsl(var(--dash-green)/0.1)]',
  },
  amber: {
    icon: 'text-[hsl(var(--dash-amber))] bg-[hsl(var(--dash-amber)/0.12)]',
    glow: 'hover:shadow-[0_0_24px_hsl(var(--dash-amber)/0.18)]',
    badge: 'text-[hsl(var(--dash-amber))] bg-[hsl(var(--dash-amber)/0.1)]',
  },
};

const StatCard = ({ data, index }: StatCardProps) => {
  const colors = colorConfig[data.color];
  const isPositive = data.change >= 0;

  return (
    <div
      className={cn(
        'relative flex flex-col gap-4 p-5 rounded-xl bg-[hsl(var(--card))] border border-[hsl(var(--border))] transition-all duration-300 cursor-default',
        colors.glow,
        'hover:-translate-y-0.5'
      )}
      style={{ animationDelay: `${index * 80}ms` }}
    >
      {/* Top row */}
      <div className="flex items-start justify-between">
        <div className={cn('flex items-center justify-center w-10 h-10 rounded-lg', colors.icon)}>
          {iconComponents[data.icon]}
        </div>
        <span
          className={cn(
            'flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold',
            isPositive
              ? 'text-[hsl(var(--dash-green))] bg-[hsl(var(--dash-green)/0.1)]'
              : 'text-[hsl(var(--dash-red))] bg-[hsl(var(--dash-red)/0.1)]'
          )}
        >
          {isPositive ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
          {isPositive ? '+' : ''}{data.change}%
        </span>
      </div>

      {/* Value */}
      <div>
        <p className="text-2xl font-bold text-[hsl(var(--dash-text-primary))] tracking-tight" style={{ fontFamily: "'Space Grotesk', sans-serif" }}>
          {data.value}
        </p>
        <p className="mt-0.5 text-sm text-[hsl(var(--muted-foreground))]">{data.label}</p>
      </div>

      {/* Footer */}
      <p className="text-xs text-[hsl(var(--muted-foreground))]">
        <span className={cn('font-medium', isPositive ? 'text-[hsl(var(--dash-green))]' : 'text-[hsl(var(--dash-red))]')}>
          {isPositive ? '↑' : '↓'} {Math.abs(data.change)}%
        </span>{' '}
        {data.changeLabel}
      </p>

      {/* Subtle bottom accent line */}
      <div
        className="absolute bottom-0 left-4 right-4 h-px rounded-full opacity-30"
        style={{
          background:
            data.color === 'red'
              ? 'hsl(var(--dash-red))'
              : data.color === 'blue'
              ? 'hsl(var(--dash-blue))'
              : data.color === 'green'
              ? 'hsl(var(--dash-green))'
              : 'hsl(var(--dash-amber))',
        }}
      />
    </div>
  );
};

export default StatCard;
