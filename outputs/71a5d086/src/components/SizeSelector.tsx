import { Size } from "@/utils/productData";

interface SizeSelectorProps {
  sizes: Size[];
  selected: string | null;
  onSelect: (size: string) => void;
}

export default function SizeSelector({ sizes, selected, onSelect }: SizeSelectorProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-body text-sm font-semibold uppercase tracking-widest text-muted-foreground">
          Size (US)
        </span>
        {selected ? (
          <span className="font-body text-sm font-medium text-brand-red">
            Selected: {selected}
          </span>
        ) : (
          <button className="font-body text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground transition-colors">
            Size Guide
          </button>
        )}
      </div>

      <div className="grid grid-cols-6 gap-2">
        {sizes.map((size) => {
          const isSelected = selected === size.label;
          const isUnavailable = !size.available;

          return (
            <button
              key={size.label}
              disabled={isUnavailable}
              onClick={() => onSelect(size.label)}
              className={`relative flex h-11 items-center justify-center rounded font-body text-sm font-medium transition-all duration-200
                ${isUnavailable
                  ? "cursor-not-allowed border border-border text-muted-foreground/30"
                  : isSelected
                  ? "border-2 border-brand-red bg-brand-red text-white shadow-lg shadow-brand-red/20"
                  : "border border-border bg-brand-surface text-foreground hover:border-brand-red/50 hover:bg-brand-surface-raised"
                }`}
            >
              {size.label}
              {isUnavailable && (
                <span className="pointer-events-none absolute inset-0 flex items-center justify-center">
                  <span className="absolute h-px w-[65%] rotate-45 bg-muted-foreground/30" />
                </span>
              )}
            </button>
          );
        })}
      </div>

      {!sizes.some((s) => s.available) && (
        <p className="font-body text-xs text-brand-red">
          This product is currently out of stock.
        </p>
      )}
    </div>
  );
}
