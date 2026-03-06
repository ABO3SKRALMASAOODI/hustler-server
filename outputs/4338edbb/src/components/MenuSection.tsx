import { useState } from "react";
import { menuCategories } from "@/data/menuData";
import MenuItemCard from "@/components/MenuItemCard";

const MenuSection = () => {
  const [activeCategory, setActiveCategory] = useState(menuCategories[0].id);

  const currentCategory = menuCategories.find((c) => c.id === activeCategory)!;

  return (
    <section id="menu" className="py-24 md:py-32 bg-background">
      <div className="container mx-auto px-6">
        {/* Section header */}
        <div className="text-center mb-16">
          <p className="font-body text-xs tracking-[0.4em] uppercase text-gold mb-4">
            Crafted with Passion
          </p>
          <h2 className="font-display font-bold text-4xl sm:text-5xl md:text-6xl text-foreground mb-6">
            Our Menu
          </h2>
          <div className="ornament-divider max-w-xs mx-auto">
            <span className="text-gold text-base px-2">✦</span>
          </div>
          <p className="mt-6 font-body text-muted-foreground max-w-xl mx-auto leading-relaxed">
            Every dish is a conversation between season and craft — sourced locally,
            finished with intention, and served with care.
          </p>
        </div>

        {/* Category tabs */}
        <div className="flex flex-wrap justify-center gap-2 mb-12">
          {menuCategories.map((cat) => (
            <button
              key={cat.id}
              onClick={() => setActiveCategory(cat.id)}
              className={`px-5 py-2.5 font-body text-sm tracking-widest uppercase rounded-sm transition-all duration-250 border ${
                activeCategory === cat.id
                  ? "bg-crimson border-crimson text-primary-foreground shadow-lg shadow-crimson/20"
                  : "bg-transparent border-border text-muted-foreground hover:border-gold/50 hover:text-foreground"
              }`}
            >
              {cat.label}
            </button>
          ))}
        </div>

        {/* Items grid */}
        <div
          key={activeCategory}
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6 animate-fade-in"
        >
          {currentCategory.items.map((item, index) => (
            <div
              key={item.id}
              className="animate-fade-in-up"
              style={{ animationDelay: `${index * 80}ms` }}
            >
              <MenuItemCard item={item} />
            </div>
          ))}
        </div>

        {/* Dietary notes */}
        <div className="mt-12 pt-8 border-t border-border flex flex-wrap justify-center gap-6 text-xs font-body text-muted-foreground tracking-wide">
          {[
            { color: "bg-crimson", label: "Chef's Pick" },
            { color: "bg-gold", label: "New" },
            { color: "bg-green-800/70", label: "Vegetarian" },
            { color: "border border-gold/40 bg-surface", label: "Signature" },
          ].map(({ color, label }) => (
            <span key={label} className="flex items-center gap-2">
              <span className={`inline-block w-2.5 h-2.5 rounded-sm ${color}`} />
              {label}
            </span>
          ))}
          <span className="text-muted-foreground/60">
            · Please inform us of any allergies or dietary requirements
          </span>
        </div>
      </div>
    </section>
  );
};

export default MenuSection;
