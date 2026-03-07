import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import { conversionData } from '@/utils/dashboardData';

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; name: string; color: string }>;
  label?: string;
}

const CustomTooltip = ({ active, payload, label }: CustomTooltipProps) => {
  if (!active || !payload || !payload.length) return null;

  const conversions = payload.find((p) => p.name === 'conversions')?.value ?? 0;
  const leads = payload.find((p) => p.name === 'leads')?.value ?? 0;
  const rate = leads > 0 ? ((conversions / leads) * 100).toFixed(1) : '0';

  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-4 py-3 shadow-xl text-sm">
      <p className="font-semibold text-[hsl(var(--dash-text-primary))] mb-2" style={{ fontFamily: "'Space Grotesk', sans-serif" }}>
        {label}
      </p>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2 text-xs mb-1">
          <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: entry.color }} />
          <span className="text-[hsl(var(--muted-foreground))] capitalize">{entry.name}:</span>
          <span className="font-semibold text-[hsl(var(--dash-text-primary))]">
            {entry.value.toLocaleString()}
          </span>
        </div>
      ))}
      <div className="mt-2 pt-2 border-t border-[hsl(var(--border))] text-xs text-[hsl(var(--muted-foreground))]">
        Conv. rate: <span className="font-semibold text-[hsl(var(--dash-green))]">{rate}%</span>
      </div>
    </div>
  );
};

const ConversionsBarChart = () => {
  return (
    <div className="flex flex-col gap-4 p-5 rounded-xl bg-[hsl(var(--card))] border border-[hsl(var(--border))] h-full">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-[hsl(var(--dash-text-primary))]" style={{ fontFamily: "'Space Grotesk', sans-serif" }}>
            Conversions by Channel
          </h3>
          <p className="text-xs text-[hsl(var(--muted-foreground))] mt-0.5">Leads vs converted · Last 30 days</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5 text-xs text-[hsl(var(--muted-foreground))]">
            <span className="w-2.5 h-2.5 rounded-sm bg-[hsl(var(--dash-red)/0.5)] inline-block" />
            Leads
          </span>
          <span className="flex items-center gap-1.5 text-xs text-[hsl(var(--muted-foreground))]">
            <span className="w-2.5 h-2.5 rounded-sm bg-[hsl(var(--dash-red))] inline-block" />
            Converted
          </span>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={245}>
        <BarChart
          data={conversionData}
          margin={{ top: 4, right: 4, left: -10, bottom: 0 }}
          barCategoryGap="30%"
          barGap={3}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(222, 15%, 16%)" vertical={false} />
          <XAxis
            dataKey="channel"
            tick={{ fill: 'hsl(215, 12%, 50%)', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: 'hsl(215, 12%, 50%)', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ fill: 'hsl(222, 15%, 14%)', radius: 4 }} />
          <Bar dataKey="leads" name="leads" fill="hsl(0, 78%, 57%)" fillOpacity={0.35} radius={[4, 4, 0, 0]} />
          <Bar dataKey="conversions" name="conversions" fill="hsl(0, 78%, 57%)" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};

export default ConversionsBarChart;
