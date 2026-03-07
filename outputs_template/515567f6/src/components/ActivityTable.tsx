import { useState } from 'react';
import { activityData, type ActivityItem } from '@/utils/dashboardData';
import { cn } from '@/lib/utils';
import { ChevronUp, ChevronDown, Search } from 'lucide-react';

const statusConfig: Record<ActivityItem['status'], { label: string; className: string; dot: string }> = {
  completed: {
    label: 'Completed',
    className: 'text-[hsl(var(--dash-green))] bg-[hsl(var(--dash-green)/0.1)] border-[hsl(var(--dash-green)/0.2)]',
    dot: 'bg-[hsl(var(--dash-green))]',
  },
  pending: {
    label: 'Pending',
    className: 'text-[hsl(var(--dash-amber))] bg-[hsl(var(--dash-amber)/0.1)] border-[hsl(var(--dash-amber)/0.2)]',
    dot: 'bg-[hsl(var(--dash-amber))]',
  },
  processing: {
    label: 'Processing',
    className: 'text-[hsl(var(--dash-blue))] bg-[hsl(var(--dash-blue)/0.1)] border-[hsl(var(--dash-blue)/0.2)]',
    dot: 'bg-[hsl(var(--dash-blue))]',
  },
  failed: {
    label: 'Failed',
    className: 'text-[hsl(var(--dash-red))] bg-[hsl(var(--dash-red)/0.1)] border-[hsl(var(--dash-red)/0.2)]',
    dot: 'bg-[hsl(var(--dash-red))]',
  },
};

type SortKey = 'user' | 'amount' | 'status' | 'timestamp';
type SortDir = 'asc' | 'desc';

const ActivityTable = () => {
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('timestamp');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  const filtered = activityData
    .filter(
      (item) =>
        item.user.toLowerCase().includes(search.toLowerCase()) ||
        item.action.toLowerCase().includes(search.toLowerCase()) ||
        item.email.toLowerCase().includes(search.toLowerCase())
    )
    .sort((a, b) => {
      const multiplier = sortDir === 'asc' ? 1 : -1;
      if (sortKey === 'amount') {
        const aNum = parseFloat(a.amount.replace(/[$,]/g, ''));
        const bNum = parseFloat(b.amount.replace(/[$,]/g, ''));
        return (aNum - bNum) * multiplier;
      }
      return a[sortKey].localeCompare(b[sortKey]) * multiplier;
    });

  const SortIcon = ({ col }: { col: SortKey }) => (
    <span className="inline-flex flex-col ml-1 opacity-40">
      <ChevronUp size={9} className={cn(sortKey === col && sortDir === 'asc' && 'opacity-100 text-[hsl(var(--dash-red))]')} />
      <ChevronDown size={9} className={cn(sortKey === col && sortDir === 'desc' && 'opacity-100 text-[hsl(var(--dash-red))]', '-mt-1')} />
    </span>
  );

  return (
    <div className="flex flex-col gap-4 p-5 rounded-xl bg-[hsl(var(--card))] border border-[hsl(var(--border))]">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-base font-semibold text-[hsl(var(--dash-text-primary))]" style={{ fontFamily: "'Space Grotesk', sans-serif" }}>
            Recent Activity
          </h3>
          <p className="text-xs text-[hsl(var(--muted-foreground))] mt-0.5">Latest transactions and events</p>
        </div>
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[hsl(var(--muted-foreground))]" />
          <input
            type="text"
            placeholder="Search activity..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 pr-3 py-1.5 text-xs rounded-lg bg-[hsl(var(--secondary))] border border-[hsl(var(--border))] text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))] focus:outline-none focus:border-[hsl(var(--dash-red)/0.5)] focus:ring-1 focus:ring-[hsl(var(--dash-red)/0.3)] w-44 transition-all"
          />
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto -mx-1">
        <table className="w-full text-sm min-w-[640px]">
          <thead>
            <tr className="border-b border-[hsl(var(--border))]">
              <th
                className="text-left pb-3 px-3 text-xs font-medium text-[hsl(var(--muted-foreground))] cursor-pointer hover:text-[hsl(var(--foreground))] select-none whitespace-nowrap"
                onClick={() => handleSort('user')}
              >
                User <SortIcon col="user" />
              </th>
              <th className="text-left pb-3 px-3 text-xs font-medium text-[hsl(var(--muted-foreground))] whitespace-nowrap">
                Action
              </th>
              <th
                className="text-left pb-3 px-3 text-xs font-medium text-[hsl(var(--muted-foreground))] cursor-pointer hover:text-[hsl(var(--foreground))] select-none whitespace-nowrap"
                onClick={() => handleSort('amount')}
              >
                Amount <SortIcon col="amount" />
              </th>
              <th
                className="text-left pb-3 px-3 text-xs font-medium text-[hsl(var(--muted-foreground))] cursor-pointer hover:text-[hsl(var(--foreground))] select-none whitespace-nowrap"
                onClick={() => handleSort('status')}
              >
                Status <SortIcon col="status" />
              </th>
              <th
                className="text-left pb-3 px-3 text-xs font-medium text-[hsl(var(--muted-foreground))] cursor-pointer hover:text-[hsl(var(--foreground))] select-none whitespace-nowrap"
                onClick={() => handleSort('timestamp')}
              >
                Time <SortIcon col="timestamp" />
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-8 text-center text-sm text-[hsl(var(--muted-foreground))]">
                  No activity matches your search.
                </td>
              </tr>
            ) : (
              filtered.map((item, i) => (
                <tr
                  key={item.id}
                  className={cn(
                    'border-b border-[hsl(var(--border)/0.5)] hover:bg-[hsl(var(--secondary))] transition-colors duration-100',
                    i === filtered.length - 1 && 'border-b-0'
                  )}
                >
                  {/* User */}
                  <td className="py-3 px-3">
                    <div className="flex items-center gap-2.5">
                      <div className="w-8 h-8 rounded-full bg-[hsl(var(--dash-red)/0.15)] border border-[hsl(var(--dash-red)/0.2)] flex items-center justify-center text-[hsl(var(--dash-red))] text-xs font-semibold shrink-0">
                        {item.avatar}
                      </div>
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-[hsl(var(--dash-text-primary))] truncate">{item.user}</p>
                        <p className="text-[10px] text-[hsl(var(--muted-foreground))] truncate">{item.email}</p>
                      </div>
                    </div>
                  </td>
                  {/* Action */}
                  <td className="py-3 px-3">
                    <span className="text-xs text-[hsl(var(--dash-text-secondary))]">{item.action}</span>
                  </td>
                  {/* Amount */}
                  <td className="py-3 px-3">
                    <span className="text-xs font-semibold text-[hsl(var(--dash-text-primary))]">{item.amount}</span>
                  </td>
                  {/* Status */}
                  <td className="py-3 px-3">
                    <span
                      className={cn(
                        'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-medium border',
                        statusConfig[item.status].className
                      )}
                    >
                      <span className={cn('w-1.5 h-1.5 rounded-full', statusConfig[item.status].dot)} />
                      {statusConfig[item.status].label}
                    </span>
                  </td>
                  {/* Time */}
                  <td className="py-3 px-3">
                    <span className="text-xs text-[hsl(var(--muted-foreground))]">{item.timestamp}</span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ActivityTable;
