import { useState } from "react";
import { Star, Package, RefreshCw, Shield, ChevronDown, ChevronUp, Share2, Truck } from "lucide-react";
import { useCart } from "@/hooks/useCart";
import { mainProduct, relatedProducts } from "@/utils/productData";
import Navbar from "@/components/Navbar";
import ImageGallery from "@/components/ImageGallery";
import SizeSelector from "@/components/SizeSelector";
import AddToCartButton from "@/components/AddToCartButton";
import RelatedProducts from "@/components/RelatedProducts";

const TABS = ["Description", "Details", "Reviews"] as const;
type Tab = (typeof TABS)[number];

export default function Index() {
  const { itemCount, addItem } = useCart();
  const [selectedSize, setSelectedSize] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("Description");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [quantity, setQuantity] = useState(1);

  const discount = mainProduct.originalPrice
    ? Math.round(((mainProduct.originalPrice - mainProduct.price) / mainProduct.originalPrice) * 100)
    : null;

  const handleAddToCart = () => {
    if (!selectedSize) return;
    for (let i = 0; i < quantity; i++) {
      addItem({
        productId: mainProduct.id,
        productName: mainProduct.name,
        size: selectedSize,
        price: mainProduct.price,
      });
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <Navbar cartCount={itemCount} />

      {/* Breadcrumb */}
      <div className="mx-auto max-w-screen-xl px-4 py-3 md:px-8">
        <nav className="flex items-center gap-2 font-body text-xs text-muted-foreground">
          <a href="#" className="hover:text-foreground transition-colors">Home</a>
          <span>/</span>
          <a href="#" className="hover:text-foreground transition-colors">{mainProduct.category}</a>
          <span>/</span>
          <span className="text-foreground">{mainProduct.name}</span>
        </nav>
      </div>

      {/* Product Section */}
      <main className="mx-auto max-w-screen-xl px-4 pb-8 md:px-8">
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-2 lg:gap-16">

          {/* LEFT — Image Gallery */}
          <div className="animate-fade-in">
            <ImageGallery images={mainProduct.images} productName={mainProduct.name} />
          </div>

          {/* RIGHT — Product Info */}
          <div className="flex flex-col gap-6 animate-slide-in-right">

            {/* Brand + Badges */}
            <div className="flex items-center justify-between">
              <span className="font-body text-xs font-bold uppercase tracking-widest text-brand-red">
                {mainProduct.brand}
              </span>
              <div className="flex items-center gap-2">
                {mainProduct.badge && (
                  <span className="rounded bg-brand-red px-2.5 py-0.5 font-display text-xs tracking-widest text-white">
                    {mainProduct.badge}
                  </span>
                )}
                <button className="flex h-8 w-8 items-center justify-center rounded-full border border-border text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground">
                  <Share2 size={14} />
                </button>
              </div>
            </div>

            {/* Title */}
            <div>
              <h1 className="font-display text-5xl leading-none text-foreground md:text-6xl">
                {mainProduct.name}
              </h1>
              <p className="mt-1 font-body text-sm text-muted-foreground">
                {mainProduct.colorway} · {mainProduct.category}
              </p>
            </div>

            {/* Rating */}
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Star
                    key={i}
                    size={15}
                    className={
                      i < Math.round(mainProduct.rating)
                        ? "fill-brand-red text-brand-red"
                        : "fill-muted text-muted"
                    }
                  />
                ))}
              </div>
              <span className="font-body text-sm font-semibold text-foreground">
                {mainProduct.rating}
              </span>
              <span className="font-body text-sm text-muted-foreground">
                ({mainProduct.reviewCount} reviews)
              </span>
            </div>

            {/* Price */}
            <div className="flex items-end gap-4">
              <span className="font-display text-5xl text-foreground leading-none">
                ${mainProduct.price}
              </span>
              {mainProduct.originalPrice && (
                <>
                  <span className="mb-1 font-body text-lg text-muted-foreground line-through">
                    ${mainProduct.originalPrice}
                  </span>
                  <span className="mb-1 rounded bg-brand-red/10 px-2 py-0.5 font-body text-sm font-semibold text-brand-red">
                    -{discount}%
                  </span>
                </>
              )}
            </div>

            {/* Divider */}
            <div className="h-px bg-border" />

            {/* Size Selector */}
            <SizeSelector
              sizes={mainProduct.sizes}
              selected={selectedSize}
              onSelect={setSelectedSize}
            />

            {/* Quantity + CTA */}
            <div className="flex flex-col gap-3">
              {/* Quantity */}
              <div className="flex items-center gap-4">
                <span className="font-body text-sm font-semibold uppercase tracking-widest text-muted-foreground">
                  Qty
                </span>
                <div className="flex items-center gap-1 rounded border border-border overflow-hidden">
                  <button
                    onClick={() => setQuantity((q) => Math.max(1, q - 1))}
                    className="flex h-9 w-9 items-center justify-center font-body text-lg text-muted-foreground transition-colors hover:bg-brand-surface-raised hover:text-foreground"
                  >
                    −
                  </button>
                  <span className="flex h-9 w-10 items-center justify-center font-body text-sm font-semibold text-foreground border-x border-border">
                    {quantity}
                  </span>
                  <button
                    onClick={() => setQuantity((q) => Math.min(10, q + 1))}
                    className="flex h-9 w-9 items-center justify-center font-body text-lg text-muted-foreground transition-colors hover:bg-brand-surface-raised hover:text-foreground"
                  >
                    +
                  </button>
                </div>
              </div>

              {/* Add to Cart Button */}
              <AddToCartButton
                selectedSize={selectedSize}
                onAddToCart={handleAddToCart}
              />
            </div>

            {/* Shipping + Trust Badges */}
            <div className="grid grid-cols-3 gap-3">
              {[
                { icon: Truck, label: "Free Shipping", sub: "Orders over $150" },
                { icon: RefreshCw, label: "30-Day Returns", sub: "No questions asked" },
                { icon: Shield, label: "Authentic", sub: "100% Guaranteed" },
              ].map(({ icon: Icon, label, sub }) => (
                <div
                  key={label}
                  className="flex flex-col items-center gap-1 rounded-lg border border-border bg-brand-surface p-3 text-center"
                >
                  <Icon size={16} className="text-brand-red" />
                  <span className="font-body text-xs font-semibold text-foreground">{label}</span>
                  <span className="font-body text-[10px] text-muted-foreground">{sub}</span>
                </div>
              ))}
            </div>

            {/* Delivery Estimate */}
            <div className="flex items-center gap-3 rounded-lg border border-brand-red/20 bg-brand-red/5 px-4 py-3">
              <Package size={16} className="shrink-0 text-brand-red" />
              <p className="font-body text-sm text-muted-foreground">
                <span className="font-semibold text-foreground">Order today</span> — estimated delivery{" "}
                <span className="font-semibold text-foreground">3–5 business days</span>
              </p>
            </div>

            {/* Accordion Tabs */}
            <div className="space-y-0 border-t border-border">
              {TABS.map((tab) => (
                <div key={tab} className="border-b border-border">
                  <button
                    onClick={() => {
                      if (activeTab === tab) {
                        setDetailsOpen((prev) => !prev);
                      } else {
                        setActiveTab(tab);
                        setDetailsOpen(true);
                      }
                    }}
                    className="flex w-full items-center justify-between py-4 font-display text-base tracking-widest text-foreground transition-colors hover:text-brand-red"
                  >
                    {tab}
                    {activeTab === tab && detailsOpen ? (
                      <ChevronUp size={16} className="text-brand-red" />
                    ) : (
                      <ChevronDown size={16} className="text-muted-foreground" />
                    )}
                  </button>

                  {activeTab === tab && detailsOpen && (
                    <div className="pb-4 animate-fade-in">
                      {tab === "Description" && (
                        <p className="font-body text-sm leading-relaxed text-muted-foreground">
                          {mainProduct.description}
                        </p>
                      )}
                      {tab === "Details" && (
                        <ul className="space-y-2">
                          {mainProduct.details.map((detail, i) => (
                            <li key={i} className="flex items-start gap-2 font-body text-sm text-muted-foreground">
                              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-red" />
                              {detail}
                            </li>
                          ))}
                        </ul>
                      )}
                      {tab === "Reviews" && (
                        <div className="space-y-4">
                          {[
                            { name: "Marcus T.", rating: 5, date: "Dec 12, 2024", review: "Best running shoe I've owned. The energy return is real — I PR'd my 5K by 40 seconds." },
                            { name: "Sarah K.", rating: 5, date: "Nov 28, 2024", review: "Stunning look, even better feel. Sizing runs true. Wore them straight out of the box." },
                            { name: "Jordan L.", rating: 4, date: "Nov 15, 2024", review: "Love the design and fit. Slightly stiff at first but break-in period is quick." },
                          ].map((r) => (
                            <div key={r.name} className="rounded-lg border border-border bg-brand-surface p-4 space-y-2">
                              <div className="flex items-center justify-between">
                                <span className="font-body text-sm font-semibold text-foreground">{r.name}</span>
                                <span className="font-body text-xs text-muted-foreground">{r.date}</span>
                              </div>
                              <div className="flex gap-0.5">
                                {Array.from({ length: 5 }).map((_, i) => (
                                  <Star
                                    key={i}
                                    size={11}
                                    className={i < r.rating ? "fill-brand-red text-brand-red" : "fill-muted text-muted"}
                                  />
                                ))}
                              </div>
                              <p className="font-body text-sm text-muted-foreground">{r.review}</p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </main>

      {/* Related Products */}
      <RelatedProducts products={relatedProducts} />

      {/* Footer */}
      <footer className="border-t border-border bg-brand-surface py-12">
        <div className="mx-auto max-w-screen-xl px-4 md:px-8">
          <div className="grid grid-cols-2 gap-8 md:grid-cols-4">
            {[
              {
                heading: "Shop",
                links: ["New Arrivals", "Running", "Training", "Lifestyle", "Sale"],
              },
              {
                heading: "Support",
                links: ["Size Guide", "Shipping Info", "Returns", "FAQ", "Contact Us"],
              },
              {
                heading: "Company",
                links: ["About VORTEK", "Careers", "Press", "Sustainability"],
              },
              {
                heading: "Connect",
                links: ["Instagram", "Twitter/X", "TikTok", "YouTube", "Newsletter"],
              },
            ].map((col) => (
              <div key={col.heading} className="space-y-3">
                <h4 className="font-display text-sm tracking-widest text-foreground">
                  {col.heading}
                </h4>
                <ul className="space-y-2">
                  {col.links.map((link) => (
                    <li key={link}>
                      <a
                        href="#"
                        className="font-body text-sm text-muted-foreground transition-colors hover:text-foreground"
                      >
                        {link}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          <div className="mt-12 flex flex-col items-center justify-between gap-4 border-t border-border pt-8 md:flex-row">
            <div className="flex items-center gap-1.5">
              <span className="font-display text-2xl tracking-widest text-foreground">VORTEK</span>
              <span className="h-1.5 w-1.5 rounded-full bg-brand-red" />
            </div>
            <p className="font-body text-xs text-muted-foreground">
              © 2024 VORTEK Athletics. All rights reserved.
            </p>
            <div className="flex gap-4">
              {["Privacy", "Terms", "Cookies"].map((item) => (
                <a
                  key={item}
                  href="#"
                  className="font-body text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  {item}
                </a>
              ))}
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
