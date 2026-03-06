import { useState, useEffect } from "react";
import { Menu, X } from "lucide-react";

const navLinks = [
  { label: "Menu", href: "#menu" },
  { label: "About", href: "#about" },
  { label: "Reservations", href: "#reservations" },
];

const Navbar = () => {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 60);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const handleNavClick = (href: string) => {
    setMenuOpen(false);
    const el = document.querySelector(href);
    if (el) el.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-500 ${
        scrolled
          ? "bg-background/95 backdrop-blur-md border-b border-border shadow-lg shadow-black/30"
          : "bg-transparent"
      }`}
    >
      <div className="container mx-auto flex items-center justify-between h-16 md:h-20">
        {/* Logo */}
        <a
          href="#hero"
          onClick={(e) => {
            e.preventDefault();
            window.scrollTo({ top: 0, behavior: "smooth" });
          }}
          className="flex flex-col leading-none"
        >
          <span className="font-display text-xl md:text-2xl font-bold text-foreground tracking-wide">
            EMBER
          </span>
          <span className="font-body text-[10px] tracking-[0.25em] uppercase text-gold">
            & Vine
          </span>
        </a>

        {/* Desktop nav */}
        <nav className="hidden md:flex items-center gap-8">
          {navLinks.map((link) => (
            <button
              key={link.href}
              onClick={() => handleNavClick(link.href)}
              className="font-body text-sm tracking-widest uppercase text-muted-foreground hover:text-foreground transition-colors duration-200 relative group"
            >
              {link.label}
              <span className="absolute -bottom-0.5 left-0 w-0 h-px bg-crimson transition-all duration-300 group-hover:w-full" />
            </button>
          ))}
          <button
            onClick={() => handleNavClick("#reservations")}
            className="ml-4 px-5 py-2 bg-crimson text-primary-foreground font-body text-sm tracking-widest uppercase hover:bg-red-600 transition-colors duration-200 rounded-sm"
          >
            Reserve a Table
          </button>
        </nav>

        {/* Mobile hamburger */}
        <button
          className="md:hidden text-foreground p-2"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label="Toggle menu"
        >
          {menuOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
      </div>

      {/* Mobile menu */}
      <div
        className={`md:hidden bg-charcoal border-t border-border overflow-hidden transition-all duration-300 ${
          menuOpen ? "max-h-80 opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <nav className="flex flex-col gap-1 px-6 py-4">
          {navLinks.map((link) => (
            <button
              key={link.href}
              onClick={() => handleNavClick(link.href)}
              className="text-left py-3 font-body text-sm tracking-widest uppercase text-muted-foreground hover:text-foreground border-b border-border/50 last:border-0 transition-colors"
            >
              {link.label}
            </button>
          ))}
          <button
            onClick={() => handleNavClick("#reservations")}
            className="mt-3 px-5 py-3 bg-crimson text-primary-foreground font-body text-sm tracking-widest uppercase rounded-sm"
          >
            Reserve a Table
          </button>
        </nav>
      </div>
    </header>
  );
};

export default Navbar;
