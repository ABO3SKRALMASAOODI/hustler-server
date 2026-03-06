import { ShoppingBag, Search, Menu, X } from "lucide-react";
import { useState } from "react";

interface NavbarProps {
  cartCount: number;
}

const navLinks = ["New Arrivals", "Running", "Training", "Lifestyle", "Sale"];

export default function Navbar({ cartCount }: NavbarProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-screen-xl items-center justify-between px-4 md:px-8">
        {/* Logo */}
        <a href="/" className="flex items-center gap-1">
          <span className="font-display text-3xl tracking-widest text-foreground">
            VORTEK
          </span>
          <span className="ml-1 h-2 w-2 rounded-full bg-brand-red" />
        </a>

        {/* Desktop Nav */}
        <nav className="hidden items-center gap-8 md:flex">
          {navLinks.map((link) => (
            <a
              key={link}
              href="#"
              className="relative font-body text-sm font-medium text-muted-foreground transition-colors hover:text-foreground group"
            >
              {link}
              <span className="absolute -bottom-0.5 left-0 h-px w-0 bg-brand-red transition-all duration-300 group-hover:w-full" />
            </a>
          ))}
        </nav>

        {/* Icons */}
        <div className="flex items-center gap-4">
          <button className="hidden text-muted-foreground transition-colors hover:text-foreground md:block">
            <Search size={20} />
          </button>
          <button className="relative text-muted-foreground transition-colors hover:text-foreground">
            <ShoppingBag size={20} />
            {cartCount > 0 && (
              <span className="absolute -right-2 -top-2 flex h-4 w-4 items-center justify-center rounded-full bg-brand-red text-[10px] font-bold text-white animate-scale-in">
                {cartCount > 9 ? "9+" : cartCount}
              </span>
            )}
          </button>
          <button
            className="text-muted-foreground transition-colors hover:text-foreground md:hidden"
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {menuOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>
      </div>

      {/* Mobile Menu */}
      {menuOpen && (
        <div className="border-t border-border bg-card px-4 pb-6 pt-4 md:hidden animate-fade-in">
          <nav className="flex flex-col gap-4">
            {navLinks.map((link) => (
              <a
                key={link}
                href="#"
                className="font-body text-base font-medium text-muted-foreground transition-colors hover:text-foreground"
                onClick={() => setMenuOpen(false)}
              >
                {link}
              </a>
            ))}
          </nav>
        </div>
      )}
    </header>
  );
}
