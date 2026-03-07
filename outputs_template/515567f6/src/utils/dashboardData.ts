export interface RevenueDataPoint {
  month: string;
  revenue: number;
  target: number;
}

export interface ConversionDataPoint {
  channel: string;
  conversions: number;
  leads: number;
}

export interface ActivityItem {
  id: string;
  user: string;
  email: string;
  action: string;
  amount: string;
  status: 'completed' | 'pending' | 'failed' | 'processing';
  timestamp: string;
  avatar: string;
}

export interface StatCardData {
  label: string;
  value: string;
  change: number;
  changeLabel: string;
  icon: string;
  color: 'red' | 'green' | 'blue' | 'amber';
}

export const revenueData: RevenueDataPoint[] = [
  { month: 'Jan', revenue: 42000, target: 38000 },
  { month: 'Feb', revenue: 51000, target: 42000 },
  { month: 'Mar', revenue: 47800, target: 45000 },
  { month: 'Apr', revenue: 63200, target: 50000 },
  { month: 'May', revenue: 58400, target: 55000 },
  { month: 'Jun', revenue: 71900, target: 60000 },
  { month: 'Jul', revenue: 68300, target: 65000 },
  { month: 'Aug', revenue: 82100, target: 70000 },
  { month: 'Sep', revenue: 76500, target: 75000 },
  { month: 'Oct', revenue: 91200, target: 80000 },
  { month: 'Nov', revenue: 88700, target: 85000 },
  { month: 'Dec', revenue: 104500, target: 95000 },
];

export const conversionData: ConversionDataPoint[] = [
  { channel: 'Organic', conversions: 1842, leads: 4210 },
  { channel: 'Paid Ads', conversions: 2630, leads: 7100 },
  { channel: 'Email', conversions: 1190, leads: 2900 },
  { channel: 'Referral', conversions: 980, leads: 1750 },
  { channel: 'Social', conversions: 760, leads: 3200 },
  { channel: 'Direct', conversions: 1450, leads: 2800 },
];

export const activityData: ActivityItem[] = [
  {
    id: '1',
    user: 'Marcus Chen',
    email: 'marcus.chen@acme.io',
    action: 'Upgraded to Enterprise',
    amount: '$2,400.00',
    status: 'completed',
    timestamp: '2 min ago',
    avatar: 'MC',
  },
  {
    id: '2',
    user: 'Sarah Williams',
    email: 'swilliams@techcorp.com',
    action: 'New subscription started',
    amount: '$149.00',
    status: 'completed',
    timestamp: '18 min ago',
    avatar: 'SW',
  },
  {
    id: '3',
    user: 'Jordan Park',
    email: 'j.park@growthly.co',
    action: 'Payment processing',
    amount: '$899.00',
    status: 'processing',
    timestamp: '34 min ago',
    avatar: 'JP',
  },
  {
    id: '4',
    user: 'Aisha Okonkwo',
    email: 'aisha@venturex.io',
    action: 'Invoice overdue',
    amount: '$3,200.00',
    status: 'failed',
    timestamp: '1 hr ago',
    avatar: 'AO',
  },
  {
    id: '5',
    user: 'Thomas Reed',
    email: 'thomas.reed@deltasoft.com',
    action: 'Pending verification',
    amount: '$620.00',
    status: 'pending',
    timestamp: '2 hr ago',
    avatar: 'TR',
  },
  {
    id: '6',
    user: 'Priya Sharma',
    email: 'p.sharma@cloudbase.dev',
    action: 'Annual plan renewed',
    amount: '$1,788.00',
    status: 'completed',
    timestamp: '3 hr ago',
    avatar: 'PS',
  },
  {
    id: '7',
    user: 'Felix Müller',
    email: 'fmuller@nexgen.eu',
    action: 'New subscription started',
    amount: '$99.00',
    status: 'completed',
    timestamp: '5 hr ago',
    avatar: 'FM',
  },
  {
    id: '8',
    user: 'Layla Hassan',
    email: 'layla.h@sparkdata.ai',
    action: 'Trial conversion',
    amount: '$299.00',
    status: 'pending',
    timestamp: '6 hr ago',
    avatar: 'LH',
  },
];

export const statCards: StatCardData[] = [
  {
    label: 'Total Revenue',
    value: '$847,291',
    change: 12.4,
    changeLabel: 'vs last month',
    icon: 'DollarSign',
    color: 'red',
  },
  {
    label: 'Active Users',
    value: '24,830',
    change: 8.1,
    changeLabel: 'vs last month',
    icon: 'Users',
    color: 'blue',
  },
  {
    label: 'Conversions',
    value: '8,852',
    change: -2.3,
    changeLabel: 'vs last month',
    icon: 'TrendingUp',
    color: 'green',
  },
  {
    label: 'Avg. Session',
    value: '4m 37s',
    change: 5.7,
    changeLabel: 'vs last month',
    icon: 'Clock',
    color: 'amber',
  },
];

export const navItems = [
  { label: 'Dashboard', icon: 'LayoutDashboard', path: '/', active: true },
  { label: 'Analytics', icon: 'BarChart2', path: '#', active: false },
  { label: 'Revenue', icon: 'DollarSign', path: '#', active: false },
  { label: 'Users', icon: 'Users', path: '#', active: false },
  { label: 'Campaigns', icon: 'Megaphone', path: '#', active: false },
  { label: 'Reports', icon: 'FileText', path: '#', active: false },
  { label: 'Settings', icon: 'Settings', path: '#', active: false },
];
