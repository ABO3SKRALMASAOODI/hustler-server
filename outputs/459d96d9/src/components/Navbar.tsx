import { useState, useEffect } from "react";
import { Menu, X, Zap } from "lucide-react";

const navLinks = [
  { label: "Features", href: "#features" },
  { label: "Pricing", href: "#pricing" },
  { label: "Docs", href: "#" },
  { label: "Blog", href: "#" },
];

const Navbar = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const handleNavClick = (href: string) => {
    setIsOpen(false);
    if (href.startsWith("#")) {
      const el = document.querySelector(href);
      if (el) el.scrollIntoView({ behavior: "smooth" });
    }
  };

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled ? "nav-glass" : "bg-transparent"
      }`}
    >
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16 md:h-18">
          {/* Logo */}
          <a href="/" className="flex items-center gap-2 group">
            <div className="w-8 h-8 rounded-lg bg-brand-red flex items-center justify-center glow-red-sm group-hover:glow-red transition-all duration-300">
              <Zap className="w-4 h-4 text-white fill-white" />
            </div>
            <span className="font-display font-700 text-xl tracking-tight text-white">
              Apexio
            </span>
          </a>

          {/* Desktop Nav */}
          <nav className="hidden md:flex items-center gap-8">
            {navLinks.map((link) => (
              <button
                key={link.label}
                onClick={() => handleNavClick(link.href)}
                className="text-sm font-body font-medium text-white/60 hover:text-white transition-colors duration-200 cursor-pointer"
              >
                {link.label}
              </button>
            ))}
          </nav>

          {/* Desktop CTA */}
          <div className="hidden md:flex items-center gap-3">
            <button className="text-sm font-medium text-white/70 hover:text-white transition-colors duration-200 px-4 py-2">
              Sign in
            </button>
            <button
              onClick={() => handleNavClick("#pricing")}
              className="text-sm font-semibold bg-brand-red hover:bg-brand-red-dark text-white px-5 py-2 rounded-lg transition-all duration-200 glow-red-sm hover:glow-red"
            >
              Get Started
            </button>
          </div>

          {/* Mobile menu button */}
          <button
            className="md:hidden text-white/70 hover:text-white transition-colors"
            onClick={() => setIsOpen(!isOpen)}
            aria-label="Toggle menu"
          >
            {isOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      <div
        className={`md:hidden transition-all duration-300 overflow-hidden ${
          isOpen ? "max-h-96 opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="nav-glass border-t border-white/5 px-4 pt-4 pb-6 space-y-1">
          {navLinks.map((link) => (
            <button
              key={link.label}
              onClick={() => handleNavClick(link.href)}
              className="block w-full text-left px-3 py-3 text-sm font-medium text-white/70 hover:text-white hover:bg-white/5 rounded-lg transition-all duration-200"
            >
              {link.label}
            </button>
          ))}
          <div className="pt-4 flex flex-col gap-3">
            <button className="w-full text-sm font-medium text-white/70 border border-white/10 hover:border-white/20 hover:text-white px-4 py-2.5 rounded-lg transition-all duration-200">
              Sign in
            </button>
            <button
              onClick={() => handleNavClick("#pricing")}
              className="w-full text-sm font-semibold bg-brand-red hover:bg-brand-red-dark text-white px-4 py-2.5 rounded-lg transition-all duration-200"
            >
              Get Started
            </button>
          </div>
        </div>
      </div>
    </header>
  );
};

export default Navbar;
