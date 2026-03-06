import { useState } from 'react';
import Sidebar from '@/components/Sidebar';
import StatCard from '@/components/StatCard';
import RevenueLineChart from '@/components/RevenueLineChart';
import ConversionsBarChart from '@/components/ConversionsBarChart';
import ActivityTable from '@/components/ActivityTable';
import { statCards } from '@/utils/dashboardData';
import {
  Bell,
  Calendar,
  RefreshCw,
  Download,
  ChevronDown,
} from 'lucide-react';

const Index = () => {
  const [activeSection, setActiveSection] = useState('dashboard');
  const [isRefreshing, setIsRefreshing] = useState(false);

  const handleRefresh = () => {
    setIsRefreshing(true);
    setTimeout(() => setIsRefreshing(false), 1000);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-[hsl(var(--background))]">
      {/* Sidebar */}
      <Sidebar activeSection={activeSection} onNavigate={setActiveSection} />

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top bar */}
        <header className="flex items-center justify-between px-6 py-4 border-b border-[hsl(var(--border))] bg-[hsl(var(--background))] shrink-0">
          <div>
            <h1
              className="text-xl font-bold text-[hsl(var(--dash-text-primary))] capitalize"
              style={{ fontFamily: "'Space Grotesk', sans-serif" }}
            >
              {activeSection === 'dashboard' ? 'Overview Dashboard' : activeSection}
            </h1>
            <p className="text-xs text-[hsl(var(--muted-foreground))] mt-0.5">
              Monday, July 21, 2025 · Real-time data
            </p>
          </div>

          <div className="flex items-center gap-2">
            {/* Date range selector */}
            <button className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium text-[hsl(var(--muted-foreground))] bg-[hsl(var(--secondary))] border border-[hsl(var(--border))] hover:border-[hsl(var(--dash-red)/0.4)] hover:text-[hsl(var(--foreground))] transition-all">
              <Calendar size={13} />
              Last 30 days
              <ChevronDown size={12} />
            </button>

            {/* Export */}
            <button className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium text-[hsl(var(--muted-foreground))] bg-[hsl(var(--secondary))] border border-[hsl(var(--border))] hover:border-[hsl(var(--dash-red)/0.4)] hover:text-[hsl(var(--foreground))] transition-all">
              <Download size={13} />
              Export
            </button>

            {/* Refresh */}
            <button
              onClick={handleRefresh}
              className="flex items-center justify-center w-9 h-9 rounded-lg text-[hsl(var(--muted-foreground))] bg-[hsl(var(--secondary))] border border-[hsl(var(--border))] hover:border-[hsl(var(--dash-red)/0.4)] hover:text-[hsl(var(--foreground))] transition-all"
              aria-label="Refresh data"
            >
              <RefreshCw size={14} className={isRefreshing ? 'animate-spin' : ''} />
            </button>

            {/* Notifications */}
            <button className="relative flex items-center justify-center w-9 h-9 rounded-lg text-[hsl(var(--muted-foreground))] bg-[hsl(var(--secondary))] border border-[hsl(var(--border))] hover:border-[hsl(var(--dash-red)/0.4)] hover:text-[hsl(var(--foreground))] transition-all">
              <Bell size={15} />
              <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-[hsl(var(--dash-red))] border-2 border-[hsl(var(--background))]" />
            </button>
          </div>
        </header>

        {/* Dashboard body */}
        <main className="flex-1 overflow-y-auto px-6 py-6 space-y-6">

          {/* KPI Banner */}
          <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-[hsl(var(--dash-red)/0.07)] border border-[hsl(var(--dash-red)/0.2)]">
            <span className="w-2 h-2 rounded-full bg-[hsl(var(--dash-red))] animate-pulse" />
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              <span className="font-semibold text-[hsl(var(--dash-text-primary))]">Q4 target on track</span>
              &nbsp;· Revenue is 9.2% ahead of projection. Keep it up!
            </p>
          </div>

          {/* Stat Cards */}
          <section>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
              {statCards.map((card, i) => (
                <StatCard key={card.label} data={card} index={i} />
              ))}
            </div>
          </section>

          {/* Charts row */}
          <section className="grid grid-cols-1 lg:grid-cols-5 gap-4">
            <div className="lg:col-span-3">
              <RevenueLineChart />
            </div>
            <div className="lg:col-span-2">
              <ConversionsBarChart />
            </div>
          </section>

          {/* Activity table */}
          <section>
            <ActivityTable />
          </section>

          {/* Footer */}
          <footer className="flex items-center justify-between pt-2 pb-1">
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              PulseIQ Analytics · v2.4.1
            </p>
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Last updated: <span className="text-[hsl(var(--dash-text-secondary))]">just now</span>
            </p>
          </footer>
        </main>
      </div>
    </div>
  );
};

export default Index;
