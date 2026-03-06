import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Area,
  AreaChart,
} from 'recharts';
import { revenueData } from '@/utils/dashboardData';

const formatCurrency = (value: number) =>
  value >= 1000 ? `$${(value / 1000).toFixed(0)}k` : `$${value}`;

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; name: string; color: string }>;
  label?: string;
}

const CustomTooltip = ({ active, payload, label }: CustomTooltipProps) => {
  if (!active || !payload || !payload.length) return null;

  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-4 py-3 shadow-xl text-sm">
      <p className="font-semibold text-[hsl(var(--dash-text-primary))] mb-2" style={{ fontFamily: "'Space Grotesk', sans-serif" }}>
        {label}
      </p>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2 text-xs mb-1">
          <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: entry.color }} />
          <span className="text-[hsl(var(--muted-foreground))] capitalize">{entry.name}:</span>
          <span className="font-semibold text-[hsl(var(--dash-text-primary))]">
            {formatCurrency(entry.value)}
          </span>
        </div>
      ))}
    </div>
  );
};

const RevenueLineChart = () => {
  return (
    <div className="flex flex-col gap-4 p-5 rounded-xl bg-[hsl(var(--card))] border border-[hsl(var(--border))] h-full">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-[hsl(var(--dash-text-primary))]" style={{ fontFamily: "'Space Grotesk', sans-serif" }}>
            Revenue vs Target
          </h3>
          <p className="text-xs text-[hsl(var(--muted-foreground))] mt-0.5">Full year performance · 2024</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5 text-xs text-[hsl(var(--muted-foreground))]">
            <span className="w-3 h-0.5 rounded-full bg-[hsl(var(--dash-red))] inline-block" />
            Revenue
          </span>
          <span className="flex items-center gap-1.5 text-xs text-[hsl(var(--muted-foreground))]">
            <span className="w-3 h-0.5 rounded-full bg-[hsl(var(--dash-text-secondary))] inline-block border-dashed border-b" />
            Target
          </span>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={revenueData} margin={{ top: 4, right: 4, left: -10, bottom: 0 }}>
          <defs>
            <linearGradient id="revenueGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="hsl(0, 78%, 57%)" stopOpacity={0.25} />
              <stop offset="95%" stopColor="hsl(0, 78%, 57%)" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="targetGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="hsl(215, 12%, 50%)" stopOpacity={0.12} />
              <stop offset="95%" stopColor="hsl(215, 12%, 50%)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="hsl(222, 15%, 16%)"
            vertical={false}
          />
          <XAxis
            dataKey="month"
            tick={{ fill: 'hsl(215, 12%, 50%)', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tickFormatter={formatCurrency}
            tick={{ fill: 'hsl(215, 12%, 50%)', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ stroke: 'hsl(222, 15%, 20%)', strokeWidth: 1 }} />
          <Area
            type="monotone"
            dataKey="target"
            stroke="hsl(215, 12%, 45%)"
            strokeWidth={1.5}
            strokeDasharray="5 4"
            fill="url(#targetGradient)"
            dot={false}
            activeDot={{ r: 4, fill: 'hsl(215, 12%, 55%)', stroke: 'hsl(222, 18%, 10%)', strokeWidth: 2 }}
          />
          <Area
            type="monotone"
            dataKey="revenue"
            stroke="hsl(0, 78%, 57%)"
            strokeWidth={2.5}
            fill="url(#revenueGradient)"
            dot={false}
            activeDot={{ r: 5, fill: 'hsl(0, 78%, 57%)', stroke: 'hsl(222, 18%, 10%)', strokeWidth: 2 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};

export default RevenueLineChart;
