import { useState } from 'react';
import {
  LayoutDashboard,
  BarChart2,
  DollarSign,
  Users,
  Megaphone,
  FileText,
  Settings,
  ChevronLeft,
  ChevronRight,
  Zap,
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface NavItem {
  label: string;
  icon: React.ReactNode;
  path: string;
  active: boolean;
}

interface SidebarProps {
  activeSection: string;
  onNavigate: (section: string) => void;
}

const iconMap: Record<string, React.ReactNode> = {
  LayoutDashboard: <LayoutDashboard size={18} />,
  BarChart2: <BarChart2 size={18} />,
  DollarSign: <DollarSign size={18} />,
  Users: <Users size={18} />,
  Megaphone: <Megaphone size={18} />,
  FileText: <FileText size={18} />,
  Settings: <Settings size={18} />,
};

const navItems: NavItem[] = [
  { label: 'Dashboard', icon: iconMap['LayoutDashboard'], path: 'dashboard', active: true },
  { label: 'Analytics', icon: iconMap['BarChart2'], path: 'analytics', active: false },
  { label: 'Revenue', icon: iconMap['DollarSign'], path: 'revenue', active: false },
  { label: 'Users', icon: iconMap['Users'], path: 'users', active: false },
  { label: 'Campaigns', icon: iconMap['Megaphone'], path: 'campaigns', active: false },
  { label: 'Reports', icon: iconMap['FileText'], path: 'reports', active: false },
];

const bottomItems: NavItem[] = [
  { label: 'Settings', icon: iconMap['Settings'], path: 'settings', active: false },
];

const Sidebar = ({ activeSection, onNavigate }: SidebarProps) => {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside
      className={cn(
        'relative flex flex-col h-screen bg-[hsl(var(--sidebar-background))] border-r border-[hsl(var(--sidebar-border))] transition-all duration-300 ease-in-out shrink-0',
        collapsed ? 'w-16' : 'w-60'
      )}
    >
      {/* Logo */}
      <div className={cn('flex items-center gap-3 px-4 py-5 border-b border-[hsl(var(--sidebar-border))]', collapsed && 'justify-center px-0')}>
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-[hsl(var(--dash-red))] shrink-0">
          <Zap size={16} className="text-white" />
        </div>
        {!collapsed && (
          <span className="text-[hsl(var(--dash-text-primary))] font-semibold text-base tracking-tight" style={{ fontFamily: "'Space Grotesk', sans-serif" }}>
            Pulse<span className="text-[hsl(var(--dash-red))]">IQ</span>
          </span>
        )}
      </div>

      {/* Nav items */}
      <nav className="flex-1 px-2 py-4 space-y-1 overflow-y-auto">
        {!collapsed && (
          <p className="px-3 mb-2 text-xs font-medium uppercase tracking-widest text-[hsl(var(--muted-foreground))]">
            Main Menu
          </p>
        )}
        {navItems.map((item) => {
          const isActive = activeSection === item.path;
          return (
            <button
              key={item.path}
              onClick={() => onNavigate(item.path)}
              className={cn(
                'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 group',
                collapsed && 'justify-center px-0',
                isActive
                  ? 'bg-[hsl(var(--dash-red)/0.15)] text-[hsl(var(--dash-red))] border border-[hsl(var(--dash-red)/0.25)]'
                  : 'text-[hsl(var(--sidebar-foreground))] hover:bg-[hsl(var(--sidebar-accent))] hover:text-[hsl(var(--sidebar-accent-foreground))]'
              )}
              title={collapsed ? item.label : undefined}
            >
              <span className={cn('shrink-0', isActive ? 'text-[hsl(var(--dash-red))]' : 'text-[hsl(var(--muted-foreground))] group-hover:text-[hsl(var(--sidebar-accent-foreground))]')}>
                {item.icon}
              </span>
              {!collapsed && <span>{item.label}</span>}
              {!collapsed && isActive && (
                <span className="ml-auto w-1.5 h-1.5 rounded-full bg-[hsl(var(--dash-red))]" />
              )}
            </button>
          );
        })}
      </nav>

      {/* Bottom items */}
      <div className="px-2 py-3 border-t border-[hsl(var(--sidebar-border))] space-y-1">
        {bottomItems.map((item) => {
          const isActive = activeSection === item.path;
          return (
            <button
              key={item.path}
              onClick={() => onNavigate(item.path)}
              className={cn(
                'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 group',
                collapsed && 'justify-center px-0',
                isActive
                  ? 'bg-[hsl(var(--dash-red)/0.15)] text-[hsl(var(--dash-red))]'
                  : 'text-[hsl(var(--sidebar-foreground))] hover:bg-[hsl(var(--sidebar-accent))] hover:text-[hsl(var(--sidebar-accent-foreground))]'
              )}
              title={collapsed ? item.label : undefined}
            >
              <span className="shrink-0 text-[hsl(var(--muted-foreground))] group-hover:text-[hsl(var(--sidebar-accent-foreground))]">
                {item.icon}
              </span>
              {!collapsed && <span>{item.label}</span>}
            </button>
          );
        })}

        {/* User profile chip */}
        <div className={cn('flex items-center gap-2.5 px-3 py-2.5 mt-2 rounded-lg bg-[hsl(var(--sidebar-accent))]', collapsed && 'justify-center px-0')}>
          <div className="w-7 h-7 rounded-full bg-[hsl(var(--dash-red))] flex items-center justify-center text-white text-xs font-semibold shrink-0">
            AJ
          </div>
          {!collapsed && (
            <div className="flex flex-col min-w-0">
              <span className="text-xs font-medium text-[hsl(var(--dash-text-primary))] truncate">Alex Johnson</span>
              <span className="text-[10px] text-[hsl(var(--muted-foreground))] truncate">admin@pulseiq.io</span>
            </div>
          )}
        </div>
      </div>

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="absolute -right-3 top-1/2 -translate-y-1/2 w-6 h-6 rounded-full bg-[hsl(var(--card))] border border-[hsl(var(--border))] flex items-center justify-center text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] hover:border-[hsl(var(--dash-red))] transition-all duration-150 z-10 shadow-md"
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronLeft size={12} />}
      </button>
    </aside>
  );
};

export default Sidebar;
