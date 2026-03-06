import { Instagram, Facebook, Twitter } from "lucide-react";

const Footer = () => {
  const handleNavClick = (href: string) => {
    const el = document.querySelector(href);
    if (el) el.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <footer className="bg-charcoal border-t border-border">
      {/* Top band */}
      <div className="border-b border-border/50 py-6">
        <div className="container mx-auto px-6 text-center">
          <p className="font-body text-xs tracking-[0.4em] uppercase text-gold">
            Tuesday – Sunday &nbsp;·&nbsp; 5:30 PM – 11:00 PM &nbsp;·&nbsp; 42 West 17th Street, New York
          </p>
        </div>
      </div>

      {/* Main footer */}
      <div className="container mx-auto px-6 py-16">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-12 md:gap-8">
          {/* Brand */}
          <div className="md:col-span-1">
            <div className="mb-4">
              <span className="font-display text-3xl font-black text-foreground tracking-wide">EMBER</span>
              <br />
              <span className="font-display italic text-xl text-gold">&amp; Vine</span>
            </div>
            <p className="font-body text-sm text-muted-foreground leading-relaxed max-w-xs mb-6">
              A sanctuary of fire, flavor, and fellowship in the heart of
              Chelsea. Fine dining without pretense.
            </p>
            {/* Social links */}
            <div className="flex gap-4">
              {[
                { Icon: Instagram, href: "#", label: "Instagram" },
                { Icon: Facebook, href: "#", label: "Facebook" },
                { Icon: Twitter, href: "#", label: "Twitter" },
              ].map(({ Icon, href, label }) => (
                <a
                  key={label}
                  href={href}
                  aria-label={label}
                  className="w-9 h-9 flex items-center justify-center border border-border text-muted-foreground hover:border-crimson hover:text-crimson rounded-sm transition-colors duration-200"
                >
                  <Icon size={15} />
                </a>
              ))}
            </div>
          </div>

          {/* Navigation */}
          <div>
            <h4 className="font-body text-xs tracking-[0.3em] uppercase text-gold mb-5">
              Navigate
            </h4>
            <ul className="space-y-3">
              {[
                { label: "Home", href: "#hero" },
                { label: "Menu", href: "#menu" },
                { label: "About", href: "#about" },
                { label: "Reservations", href: "#reservations" },
              ].map(({ label, href }) => (
                <li key={label}>
                  <button
                    onClick={() => {
                      if (href === "#hero") window.scrollTo({ top: 0, behavior: "smooth" });
                      else handleNavClick(href);
                    }}
                    className="font-body text-sm text-muted-foreground hover:text-foreground transition-colors duration-200"
                  >
                    {label}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* Contact & Hours */}
          <div>
            <h4 className="font-body text-xs tracking-[0.3em] uppercase text-gold mb-5">
              Contact &amp; Hours
            </h4>
            <div className="space-y-3 font-body text-sm text-muted-foreground">
              <p>
                <span className="text-foreground/70">Phone: </span>
                +1 (212) 555-0174
              </p>
              <p>
                <span className="text-foreground/70">Email: </span>
                hello@embervine.com
              </p>
              <p>
                <span className="text-foreground/70">Address: </span>
                42 West 17th Street,
                <br />
                New York, NY 10011
              </p>
              <div className="pt-2 border-t border-border/50">
                <p className="text-foreground/70 mb-1">Hours</p>
                <p>Tue – Fri: 5:30 PM – 11 PM</p>
                <p>Sat – Sun: 5:00 PM – 11:30 PM</p>
                <p className="text-muted-foreground/50 text-xs mt-1">Closed Mondays</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom bar */}
      <div className="border-t border-border/50 py-5">
        <div className="container mx-auto px-6 flex flex-col sm:flex-row items-center justify-between gap-3">
          <p className="font-body text-xs text-muted-foreground/50">
            © {new Date().getFullYear()} Ember &amp; Vine. All rights reserved.
          </p>
          <div className="flex gap-6">
            {["Privacy Policy", "Terms of Service", "Accessibility"].map((item) => (
              <a
                key={item}
                href="#"
                className="font-body text-xs text-muted-foreground/50 hover:text-muted-foreground transition-colors duration-200"
              >
                {item}
              </a>
            ))}
          </div>
        </div>
      </div>
    </footer>
  );
};

export default Footer;
