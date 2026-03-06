import { ArrowRight } from "lucide-react";
import { Product } from "@/utils/productData";
import ProductCard from "./ProductCard";

interface RelatedProductsProps {
  products: Product[];
}

export default function RelatedProducts({ products }: RelatedProductsProps) {
  return (
    <section className="border-t border-border py-16">
      <div className="mx-auto max-w-screen-xl px-4 md:px-8">
        {/* Header */}
        <div className="mb-8 flex items-end justify-between">
          <div>
            <p className="font-body text-xs font-semibold uppercase tracking-widest text-brand-red">
              You May Also Like
            </p>
            <h2 className="font-display text-4xl text-foreground md:text-5xl">
              Related Products
            </h2>
          </div>
          <a
            href="#"
            className="group hidden items-center gap-2 font-body text-sm font-medium text-muted-foreground transition-colors hover:text-foreground md:flex"
          >
            View All
            <ArrowRight
              size={16}
              className="transition-transform duration-200 group-hover:translate-x-1"
            />
          </a>
        </div>

        {/* Grid */}
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4 lg:gap-6">
          {products.map((product, i) => (
            <div
              key={product.id}
              className="animate-fade-in"
              style={{ animationDelay: `${i * 80}ms`, animationFillMode: "both" }}
            >
              <ProductCard product={product} />
            </div>
          ))}
        </div>

        {/* Mobile View All */}
        <div className="mt-8 flex justify-center md:hidden">
          <a
            href="#"
            className="flex items-center gap-2 font-body text-sm font-medium text-muted-foreground hover:text-foreground"
          >
            View All Products
            <ArrowRight size={16} />
          </a>
        </div>
      </div>
    </section>
  );
}
