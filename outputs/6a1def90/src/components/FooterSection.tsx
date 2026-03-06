import { Zap, Twitter, Github, Linkedin, Youtube } from "lucide-react";

const footerLinks = {
  Product: ["Features", "Changelog", "Roadmap", "Pricing", "Integrations"],
  Developers: ["Documentation", "API Reference", "SDK", "Status Page", "Open Source"],
  Company: ["About", "Blog", "Careers", "Press Kit", "Partners"],
  Legal: ["Privacy Policy", "Terms of Service", "Cookie Policy", "GDPR", "Security"],
};

const socials = [
  { icon: Twitter, label: "Twitter", href: "#" },
  { icon: Github, label: "GitHub", href: "#" },
  { icon: Linkedin, label: "LinkedIn", href: "#" },
  { icon: Youtube, label: "YouTube", href: "#" },
];

const FooterSection = () => {
  const scrollToSection = (href: string) => {
    const el = document.querySelector(href);
    if (el) el.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <footer className="relative border-t border-white/6 pt-20 pb-10 overflow-hidden">
      {/* Background glow */}
      <div className="absolute bottom-0 left-1/2 -translate-x-1/2 w-[500px] h-[200px] rounded-full bg-brand-red/5 blur-[100px] pointer-events-none" />

      <div className="container mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        {/* Top section */}
        <div className="grid grid-cols-2 md:grid-cols-6 gap-10 mb-16">
          {/* Brand column */}
          <div className="col-span-2">
            <a href="/" className="flex items-center gap-2 mb-5 group w-fit">
              <div className="w-8 h-8 rounded-lg bg-brand-red flex items-center justify-center glow-red-sm">
                <Zap className="w-4 h-4 text-white fill-white" />
              </div>
              <span className="font-display font-bold text-xl text-white tracking-tight">
                Apexio
              </span>
            </a>
            <p className="text-sm text-white/40 leading-relaxed font-body max-w-56 mb-6">
              The modern SaaS operations platform for engineering teams that ship fast.
            </p>
            {/* Social links */}
            <div className="flex items-center gap-3">
              {socials.map((social) => {
                const Icon = social.icon;
                return (
                  <a
                    key={social.label}
                    href={social.href}
                    aria-label={social.label}
                    className="w-9 h-9 rounded-lg border border-white/10 hover:border-brand-red/40 bg-white/5 hover:bg-brand-red/10 flex items-center justify-center transition-all duration-200 group"
                  >
                    <Icon className="w-4 h-4 text-white/40 group-hover:text-brand-red transition-colors duration-200" />
                  </a>
                );
              })}
            </div>
          </div>

          {/* Link columns */}
          {Object.entries(footerLinks).map(([category, links]) => (
            <div key={category}>
              <h4 className="font-display font-semibold text-sm text-white/80 mb-4 tracking-wide">
                {category}
              </h4>
              <ul className="space-y-3">
                {links.map((link) => (
                  <li key={link}>
                    <a
                      href="#"
                      className="text-sm text-white/35 hover:text-white/80 transition-colors duration-200 font-body"
                      onClick={(e) => {
                        if (link === "Pricing") {
                          e.preventDefault();
                          scrollToSection("#pricing");
                        } else if (link === "Features") {
                          e.preventDefault();
                          scrollToSection("#features");
                        }
                      }}
                    >
                      {link}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        {/* Newsletter */}
        <div className="card-surface rounded-2xl p-7 mb-12 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-6">
          <div>
            <h4 className="font-display font-semibold text-white mb-1">
              Stay in the loop
            </h4>
            <p className="text-sm text-white/40 font-body">
              Product updates, tips, and engineering insights — once a month.
            </p>
          </div>
          <div className="flex w-full sm:w-auto gap-2">
            <input
              type="email"
              placeholder="you@company.com"
              className="flex-1 sm:w-60 bg-white/5 border border-white/10 focus:border-brand-red/50 text-white placeholder:text-white/25 text-sm px-4 py-2.5 rounded-lg outline-none transition-colors duration-200 font-body"
            />
            <button className="px-5 py-2.5 bg-brand-red hover:bg-brand-red-dark text-white font-semibold text-sm rounded-lg transition-all duration-200 glow-red-sm whitespace-nowrap">
              Subscribe
            </button>
          </div>
        </div>

        {/* Bottom bar */}
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-8 border-t border-white/5">
          <p className="text-xs text-white/25 font-body">
            © {new Date().getFullYear()} Apexio, Inc. All rights reserved.
          </p>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse-slow" />
            <span className="text-xs text-white/25 font-body">
              All systems operational
            </span>
          </div>
        </div>
      </div>
    </footer>
  );
};

export default FooterSection;
