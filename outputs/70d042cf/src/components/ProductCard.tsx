import { ShoppingBag, Star } from "lucide-react";
import { Product } from "@/utils/productData";

interface ProductCardProps {
  product: Product;
}

export default function ProductCard({ product }: ProductCardProps) {
  return (
    <div className="group relative flex flex-col overflow-hidden rounded-lg bg-brand-surface transition-all duration-300 hover:-translate-y-1 hover:shadow-xl hover:shadow-black/40">
      {/* Image */}
      <div className="relative overflow-hidden aspect-square bg-brand-surface-raised">
        <img
          src={product.images[0]}
          alt={product.name}
          className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
        />

        {/* Badge */}
        {product.badge && (
          <span
            className={`absolute left-3 top-3 rounded px-2 py-0.5 font-display text-xs tracking-widest text-white
              ${product.badge === "SALE"
                ? "bg-brand-red"
                : product.badge === "NEW"
                ? "bg-green-600"
                : "bg-foreground/80"
              }`}
          >
            {product.badge}
          </span>
        )}

        {/* Quick Add Overlay */}
        <div className="absolute inset-x-0 bottom-0 flex translate-y-full items-center justify-center gap-2 bg-background/90 py-3 backdrop-blur-sm transition-transform duration-300 group-hover:translate-y-0">
          <ShoppingBag size={15} className="text-brand-red" />
          <span className="font-display text-sm tracking-widest text-foreground">QUICK ADD</span>
        </div>
      </div>

      {/* Info */}
      <div className="flex flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="font-body text-xs font-medium uppercase tracking-widest text-muted-foreground">
              {product.brand}
            </p>
            <h3 className="font-display text-lg leading-tight text-foreground">
              {product.name}
            </h3>
            <p className="font-body text-xs text-muted-foreground">{product.colorway}</p>
          </div>
        </div>

        {/* Rating */}
        <div className="flex items-center gap-1.5">
          <div className="flex items-center gap-0.5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Star
                key={i}
                size={11}
                className={
                  i < Math.round(product.rating)
                    ? "fill-brand-red text-brand-red"
                    : "fill-muted text-muted"
                }
              />
            ))}
          </div>
          <span className="font-body text-xs text-muted-foreground">
            ({product.reviewCount})
          </span>
        </div>

        {/* Price */}
        <div className="flex items-center gap-2">
          <span className="font-display text-xl text-foreground">${product.price}</span>
          {product.originalPrice && (
            <span className="font-body text-sm text-muted-foreground line-through">
              ${product.originalPrice}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
