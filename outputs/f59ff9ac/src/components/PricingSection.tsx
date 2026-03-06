import { useState } from "react";
import { Check, X, Zap, ArrowRight, Star } from "lucide-react";

const freeTier = {
  name: "Free",
  monthlyPrice: 0,
  annualPrice: 0,
  description: "Perfect for side projects and early-stage exploration.",
  cta: "Start for Free",
  ctaVariant: "outline" as const,
  badge: null,
  features: [
    { text: "Up to 3 projects", included: true },
    { text: "1 team member", included: true },
    { text: "5GB storage", included: true },
    { text: "Community support", included: true },
    { text: "Basic analytics (7-day retention)", included: true },
    { text: "1 CI/CD pipeline", included: true },
    { text: "Custom domains", included: false },
    { text: "SSO / SAML", included: false },
    { text: "Priority support", included: false },
    { text: "Advanced analytics", included: false },
    { text: "Audit logs", included: false },
  ],
};

const proTier = {
  name: "Pro",
  monthlyPrice: 49,
  annualPrice: 39,
  description: "For professional teams shipping production-grade products.",
  cta: "Start Pro Trial",
  ctaVariant: "primary" as const,
  badge: "Most Popular",
  features: [
    { text: "Unlimited projects", included: true },
    { text: "Up to 25 team members", included: true },
    { text: "100GB storage", included: true },
    { text: "Priority 24/7 support", included: true },
    { text: "Advanced analytics (90-day)", included: true },
    { text: "Unlimited CI/CD pipelines", included: true },
    { text: "Custom domains", included: true },
    { text: "SSO / SAML", included: true },
    { text: "Priority support", included: true },
    { text: "Advanced analytics", included: true },
    { text: "Audit logs", included: true },
  ],
};

const enterpriseTier = {
  name: "Enterprise",
  description: "Custom contracts, SLAs, and dedicated infrastructure.",
  features: [
    "Unlimited everything",
    "Dedicated infra & VPC",
    "Custom SLA",
    "HIPAA / SOC 2",
    "Dedicated CSM",
  ],
};

const PricingSection = () => {
  const [isAnnual, setIsAnnual] = useState(true);

  const getPrice = (tier: typeof freeTier) =>
    isAnnual ? tier.annualPrice : tier.monthlyPrice;

  return (
    <section id="pricing" className="relative py-28 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-transparent via-brand-red/3 to-transparent pointer-events-none" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[500px] rounded-full bg-brand-red/5 blur-[180px] pointer-events-none" />

      <div className="container mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        {/* Header */}
        <div className="max-w-3xl mx-auto text-center mb-14">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-white/10 bg-white/5 mb-6">
            <span className="text-xs font-semibold text-white/50 tracking-widest uppercase">
              Pricing
            </span>
          </div>
          <h2 className="font-display text-4xl sm:text-5xl md:text-6xl font-bold text-white leading-tight mb-5">
            Simple, transparent
            <br />
            <span className="text-gradient-red">pricing.</span>
          </h2>
          <p className="text-base text-white/50 max-w-xl mx-auto leading-relaxed font-body mb-8">
            No surprise fees. No per-seat traps. Start free and upgrade when you're ready to scale.
          </p>

          {/* Billing toggle */}
          <div className="inline-flex items-center gap-4 px-2 py-2 rounded-xl border border-white/10 bg-white/5">
            <button
              onClick={() => setIsAnnual(false)}
              className={`px-5 py-2 text-sm font-semibold rounded-lg transition-all duration-200 ${
                !isAnnual
                  ? "bg-white/10 text-white"
                  : "text-white/40 hover:text-white/70"
              }`}
            >
              Monthly
            </button>
            <button
              onClick={() => setIsAnnual(true)}
              className={`px-5 py-2 text-sm font-semibold rounded-lg transition-all duration-200 flex items-center gap-2 ${
                isAnnual
                  ? "bg-white/10 text-white"
                  : "text-white/40 hover:text-white/70"
              }`}
            >
              Annual
              <span className="text-xs font-bold text-brand-red bg-brand-red/10 border border-brand-red/20 px-2 py-0.5 rounded-full">
                −20%
              </span>
            </button>
          </div>
        </div>

        {/* Pricing cards */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-4xl mx-auto mb-8">
          {[freeTier, proTier].map((tier) => {
            const isPro = tier.name === "Pro";
            const price = getPrice(tier);

            return (
              <div
                key={tier.name}
                className={`relative flex flex-col p-8 rounded-2xl transition-all duration-300 ${
                  isPro ? "card-surface-highlight" : "card-surface"
                }`}
              >
                {/* Popular badge */}
                {tier.badge && (
                  <div className="absolute -top-4 left-1/2 -translate-x-1/2">
                    <div className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-brand-red text-white text-xs font-bold glow-red-sm">
                      <Star className="w-3 h-3 fill-white" />
                      {tier.badge}
                    </div>
                  </div>
                )}

                {/* Tier header */}
                <div className="mb-6">
                  <div className="flex items-center gap-2 mb-3">
                    {isPro && (
                      <div className="w-6 h-6 rounded-md bg-brand-red flex items-center justify-center">
                        <Zap className="w-3.5 h-3.5 text-white fill-white" />
                      </div>
                    )}
                    <h3 className="font-display font-bold text-xl text-white">{tier.name}</h3>
                  </div>
                  <p className="text-sm text-white/45 leading-relaxed font-body mb-6">
                    {tier.description}
                  </p>

                  {/* Price */}
                  <div className="flex items-end gap-2">
                    <span className="font-display font-bold text-5xl text-white">
                      ${price}
                    </span>
                    <span className="text-white/40 font-body pb-2 text-sm">
                      / month{isAnnual && price > 0 ? ", billed annually" : ""}
                    </span>
                  </div>
                  {isPro && isAnnual && (
                    <p className="text-xs text-brand-red mt-1 font-medium">
                      Save ${(tier.monthlyPrice - tier.annualPrice) * 12}/year
                    </p>
                  )}
                </div>

                {/* CTA button */}
                <button
                  className={`w-full py-3.5 rounded-xl font-semibold text-sm transition-all duration-200 mb-8 ${
                    isPro
                      ? "bg-brand-red hover:bg-brand-red-dark text-white glow-red hover:scale-[1.02] flex items-center justify-center gap-2 group"
                      : "border border-white/15 hover:border-white/25 bg-white/5 hover:bg-white/10 text-white/80 hover:text-white"
                  }`}
                >
                  {tier.cta}
                  {isPro && (
                    <ArrowRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform duration-200" />
                  )}
                </button>

                {/* Divider */}
                <div className="w-full h-px bg-white/6 mb-7" />

                {/* Features list */}
                <ul className="space-y-3.5 flex-1">
                  {tier.features.map((feature) => (
                    <li key={feature.text} className="flex items-start gap-3">
                      <div
                        className={`flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center mt-0.5 ${
                          feature.included
                            ? isPro
                              ? "bg-brand-red/20"
                              : "bg-white/8"
                            : "bg-transparent"
                        }`}
                      >
                        {feature.included ? (
                          <Check
                            className={`w-3 h-3 ${isPro ? "text-brand-red" : "text-white/50"}`}
                          />
                        ) : (
                          <X className="w-3 h-3 text-white/20" />
                        )}
                      </div>
                      <span
                        className={`text-sm font-body leading-snug ${
                          feature.included ? "text-white/70" : "text-white/25 line-through"
                        }`}
                      >
                        {feature.text}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>

        {/* Enterprise card */}
        <div className="max-w-4xl mx-auto">
          <div className="card-surface rounded-2xl p-7 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-6">
            <div className="flex-1">
              <h3 className="font-display font-bold text-xl text-white mb-1.5">
                Enterprise
              </h3>
              <p className="text-sm text-white/45 font-body mb-4 sm:mb-0">
                {enterpriseTier.description}
              </p>
              <div className="flex flex-wrap gap-2 mt-3">
                {enterpriseTier.features.map((f) => (
                  <span
                    key={f}
                    className="text-xs text-white/50 bg-white/5 border border-white/8 px-3 py-1 rounded-full font-body"
                  >
                    {f}
                  </span>
                ))}
              </div>
            </div>
            <button className="flex-shrink-0 inline-flex items-center gap-2 px-6 py-3 rounded-xl border border-white/15 hover:border-brand-red/40 bg-white/5 hover:bg-brand-red/8 text-white/80 hover:text-white font-semibold text-sm transition-all duration-200 group">
              Contact Sales
              <ArrowRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform duration-200" />
            </button>
          </div>
        </div>

        {/* Trust note */}
        <p className="text-center text-xs text-white/25 mt-8 font-body">
          No credit card required · Cancel anytime · SOC 2 Type II certified
        </p>
      </div>
    </section>
  );
};

export default PricingSection;
