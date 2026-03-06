import { ArrowRight, Play, TrendingUp, Users, Clock } from "lucide-react";

const stats = [
  { label: "Active Teams", value: "12,000+", icon: Users },
  { label: "Faster Delivery", value: "3× Speed", icon: TrendingUp },
  { label: "Uptime SLA", value: "99.99%", icon: Clock },
];

const HeroSection = () => {
  const scrollToPricing = () => {
    const el = document.querySelector("#pricing");
    if (el) el.scrollIntoView({ behavior: "smooth" });
  };

  const scrollToFeatures = () => {
    const el = document.querySelector("#features");
    if (el) el.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <section className="relative min-h-screen flex flex-col items-center justify-center pt-16 overflow-hidden">
      {/* Background layers */}
      <div className="absolute inset-0 hero-grid opacity-100" />
      <div className="absolute inset-0 hero-radial" />
      <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full bg-brand-red/5 blur-[120px] pointer-events-none" />

      {/* Floating orb */}
      <div className="absolute top-24 right-[10%] w-32 h-32 rounded-full bg-brand-red/10 blur-3xl animate-float pointer-events-none" />
      <div className="absolute bottom-32 left-[8%] w-48 h-48 rounded-full bg-brand-red/5 blur-3xl animate-float pointer-events-none" style={{ animationDelay: "3s" }} />

      <div className="container mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        <div className="max-w-5xl mx-auto text-center">
          {/* Badge */}
          <div className="animate-fade-up inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-brand-red/30 bg-brand-red/10 mb-8">
            <span className="w-2 h-2 rounded-full bg-brand-red animate-pulse-slow" />
            <span className="text-xs font-semibold text-brand-red tracking-widest uppercase">
              Now in Public Beta — 40% Off Launch Pricing
            </span>
          </div>

          {/* Headline */}
          <h1 className="animate-fade-up-delay-1 font-display text-5xl sm:text-6xl md:text-7xl lg:text-8xl font-bold leading-[0.95] tracking-tight text-white mb-6">
            Ship products
            <br />
            <span className="text-gradient-red">10× faster.</span>
          </h1>

          {/* Subheadline */}
          <p className="animate-fade-up-delay-2 max-w-2xl mx-auto text-base sm:text-lg text-white/50 leading-relaxed mb-10 font-body">
            Apexio gives your team the infrastructure, analytics, and automation workflows to build, deploy, and scale SaaS products — without the operational overhead.
          </p>

          {/* CTA Buttons */}
          <div className="animate-fade-up-delay-3 flex flex-col sm:flex-row items-center justify-center gap-4 mb-20">
            <button
              onClick={scrollToPricing}
              className="group w-full sm:w-auto inline-flex items-center justify-center gap-2 px-7 py-3.5 bg-brand-red hover:bg-brand-red-dark text-white font-semibold text-sm rounded-xl transition-all duration-200 glow-red hover:scale-[1.02]"
            >
              Start for Free
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform duration-200" />
            </button>
            <button
              onClick={scrollToFeatures}
              className="group w-full sm:w-auto inline-flex items-center justify-center gap-2 px-7 py-3.5 border border-white/10 hover:border-white/20 bg-white/5 hover:bg-white/8 text-white/80 hover:text-white font-medium text-sm rounded-xl transition-all duration-200"
            >
              <Play className="w-4 h-4 text-brand-red fill-brand-red" />
              Watch demo
            </button>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 max-w-2xl mx-auto mb-16">
            {stats.map((stat) => {
              const Icon = stat.icon;
              return (
                <div
                  key={stat.label}
                  className="flex flex-col items-center gap-1.5 py-5 px-4 card-surface rounded-2xl"
                >
                  <Icon className="w-5 h-5 text-brand-red mb-1" />
                  <span className="font-display font-bold text-2xl text-white">{stat.value}</span>
                  <span className="text-xs text-white/40 font-body">{stat.label}</span>
                </div>
              );
            })}
          </div>

          {/* Dashboard mockup */}
          <div className="relative max-w-4xl mx-auto animate-float">
            <div className="absolute inset-0 rounded-2xl bg-gradient-to-b from-brand-red/20 to-transparent blur-3xl scale-110 pointer-events-none" />
            <div className="relative rounded-2xl overflow-hidden border border-white/10 shadow-2xl">
              {/* Fake window chrome */}
              <div className="flex items-center gap-2 px-4 py-3 bg-surface border-b border-white/5">
                <div className="w-3 h-3 rounded-full bg-red-500/70" />
                <div className="w-3 h-3 rounded-full bg-yellow-500/70" />
                <div className="w-3 h-3 rounded-full bg-green-500/70" />
                <div className="flex-1 mx-4">
                  <div className="mx-auto w-48 h-5 rounded-md bg-white/5 flex items-center justify-center">
                    <span className="text-xs text-white/20">app.apexio.io/dashboard</span>
                  </div>
                </div>
              </div>
              {/* Dashboard image */}
              <img
                src="https://placehold.co/1200x600/0d0d0d/1c1c1c?text=Dashboard+Analytics+Preview"
                alt="Apexio Dashboard"
                className="w-full block"
              />
              {/* Overlay cards on the mockup */}
              <div className="absolute bottom-6 left-6 right-6 grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                  { label: "Revenue", value: "$48,290", change: "+12.4%" },
                  { label: "MAU", value: "24,381", change: "+8.1%" },
                  { label: "Churn Rate", value: "1.2%", change: "-0.4%" },
                  { label: "NPS Score", value: "72", change: "+5pts" },
                ].map((metric) => (
                  <div
                    key={metric.label}
                    className="px-3 py-2.5 rounded-xl bg-black/80 border border-white/10 backdrop-blur-sm"
                  >
                    <p className="text-[10px] text-white/40 mb-0.5 font-body">{metric.label}</p>
                    <p className="text-sm font-bold text-white font-display">{metric.value}</p>
                    <p className="text-[10px] text-green-400 font-medium">{metric.change}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom fade */}
      <div className="absolute bottom-0 left-0 right-0 h-32 bg-gradient-to-t from-black to-transparent pointer-events-none" />
    </section>
  );
};

export default HeroSection;
