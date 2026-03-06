import {
  Zap,
  BarChart3,
  Shield,
  GitBranch,
  Bell,
  Puzzle,
  ArrowRight,
} from "lucide-react";

const features = [
  {
    icon: Zap,
    title: "Instant Deployments",
    description:
      "Push to production in seconds with atomic deployments, automatic rollbacks, and zero-downtime releases.",
    tag: "Infrastructure",
  },
  {
    icon: BarChart3,
    title: "Real-Time Analytics",
    description:
      "Understand your users deeply — funnels, retention cohorts, revenue attribution, all in one live dashboard.",
    tag: "Analytics",
  },
  {
    icon: Shield,
    title: "Enterprise Security",
    description:
      "SOC 2 Type II compliant, with end-to-end encryption, SSO/SAML support, and fine-grained RBAC policies.",
    tag: "Security",
  },
  {
    icon: GitBranch,
    title: "CI/CD Pipelines",
    description:
      "Visual pipeline builder with parallel stages, conditional logic, caching layers, and automated testing gates.",
    tag: "Automation",
  },
  {
    icon: Bell,
    title: "Smart Alerting",
    description:
      "AI-powered anomaly detection with PagerDuty, Slack, and webhook integrations. Alert on what matters.",
    tag: "Monitoring",
  },
  {
    icon: Puzzle,
    title: "100+ Integrations",
    description:
      "Connect your entire stack in minutes — Stripe, GitHub, Jira, Datadog, AWS, Vercel, and many more.",
    tag: "Ecosystem",
  },
];

const FeaturesSection = () => {
  return (
    <section id="features" className="relative py-28 overflow-hidden">
      {/* Background accent */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[400px] rounded-full bg-brand-red/4 blur-[150px] pointer-events-none" />

      <div className="container mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        {/* Header */}
        <div className="max-w-3xl mx-auto text-center mb-16">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-white/10 bg-white/5 mb-6">
            <span className="text-xs font-semibold text-white/50 tracking-widest uppercase">
              Features
            </span>
          </div>
          <h2 className="font-display text-4xl sm:text-5xl md:text-6xl font-bold text-white leading-tight mb-5">
            Everything your team
            <br />
            <span className="text-gradient-red">needs to scale.</span>
          </h2>
          <p className="text-base text-white/50 max-w-xl mx-auto leading-relaxed font-body">
            Stop stitching together a dozen tools. Apexio consolidates your entire SaaS operations platform into one cohesive, powerful system.
          </p>
        </div>

        {/* Feature grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {features.map((feature, index) => {
            const Icon = feature.icon;
            const isHighlight = index === 1;
            return (
              <div
                key={feature.title}
                className={`group relative p-7 rounded-2xl transition-all duration-300 cursor-default
                  ${isHighlight
                    ? "card-surface-highlight"
                    : "card-surface hover:border-white/12"
                  }
                `}
              >
                {/* Tag */}
                <div className="flex items-center justify-between mb-5">
                  <div
                    className={`w-11 h-11 rounded-xl flex items-center justify-center transition-all duration-300
                      ${isHighlight
                        ? "bg-brand-red/20 group-hover:bg-brand-red/30"
                        : "bg-white/5 group-hover:bg-brand-red/15"
                      }
                    `}
                  >
                    <Icon
                      className={`w-5 h-5 transition-colors duration-300 ${
                        isHighlight ? "text-brand-red" : "text-white/60 group-hover:text-brand-red"
                      }`}
                    />
                  </div>
                  <span
                    className={`text-xs font-semibold px-2.5 py-1 rounded-full tracking-wide
                      ${isHighlight
                        ? "text-brand-red bg-brand-red/10 border border-brand-red/20"
                        : "text-white/30 bg-white/5"
                      }
                    `}
                  >
                    {feature.tag}
                  </span>
                </div>

                <h3 className="font-display font-semibold text-lg text-white mb-3 leading-snug">
                  {feature.title}
                </h3>
                <p className="text-sm text-white/45 leading-relaxed font-body mb-5">
                  {feature.description}
                </p>

                <div
                  className={`flex items-center gap-1.5 text-xs font-semibold transition-all duration-200
                    ${isHighlight ? "text-brand-red" : "text-white/20 group-hover:text-brand-red"}
                  `}
                >
                  Learn more
                  <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-1 transition-transform duration-200" />
                </div>

                {/* Subtle corner glow on highlight */}
                {isHighlight && (
                  <div className="absolute top-0 right-0 w-32 h-32 bg-brand-red/10 rounded-full blur-3xl pointer-events-none" />
                )}
              </div>
            );
          })}
        </div>

        {/* Bottom CTA */}
        <div className="mt-14 text-center">
          <p className="text-sm text-white/30 mb-4 font-body">
            Trusted by engineering teams at fast-growing companies
          </p>
          <div className="flex flex-wrap items-center justify-center gap-8 opacity-25">
            {["Vercel", "Linear", "Notion", "Figma", "Stripe", "GitHub"].map((name) => (
              <span key={name} className="font-display font-bold text-lg text-white tracking-tight">
                {name}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

export default FeaturesSection;
