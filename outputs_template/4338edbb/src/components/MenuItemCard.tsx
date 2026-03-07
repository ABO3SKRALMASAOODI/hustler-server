import { MenuItem } from "@/data/menuData";

const badgeStyles: Record<string, string> = {
  "Chef's Pick": "bg-crimson text-primary-foreground",
  "New": "bg-gold text-accent-foreground",
  "Vegetarian": "bg-green-800/70 text-green-200",
  "Signature": "bg-surface border border-gold/40 text-gold",
};

interface MenuItemCardProps {
  item: MenuItem;
}

const MenuItemCard = ({ item }: MenuItemCardProps) => {
  return (
    <div className="group relative flex flex-col bg-charcoal rounded-sm overflow-hidden border border-border hover:border-gold/30 transition-all duration-400 hover:-translate-y-1 hover:shadow-xl hover:shadow-black/40">
      {/* Image */}
      <div className="relative overflow-hidden aspect-[4/3]">
        <img
          src={item.image}
          alt={item.name}
          className="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105"
          loading="lazy"
        />
        {/* Image overlay */}
        <div className="absolute inset-0 bg-gradient-to-t from-charcoal/80 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />

        {/* Badge */}
        {item.badge && (
          <span
            className={`absolute top-3 left-3 px-2.5 py-1 text-[10px] font-body font-bold tracking-widest uppercase rounded-sm ${badgeStyles[item.badge]}`}
          >
            {item.badge}
          </span>
        )}
      </div>

      {/* Content */}
      <div className="flex flex-col flex-1 p-5">
        <div className="flex items-start justify-between gap-3 mb-2">
          <h3 className="font-display font-semibold text-lg text-foreground leading-tight group-hover:text-gold transition-colors duration-200">
            {item.name}
          </h3>
          <span className="font-display font-bold text-lg text-crimson whitespace-nowrap flex-shrink-0">
            {item.price}
          </span>
        </div>
        <p className="font-body text-sm text-muted-foreground leading-relaxed">
          {item.description}
        </p>
      </div>

      {/* Bottom accent line */}
      <div className="h-px bg-gradient-to-r from-transparent via-crimson/30 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
    </div>
  );
};

export default MenuItemCard;
