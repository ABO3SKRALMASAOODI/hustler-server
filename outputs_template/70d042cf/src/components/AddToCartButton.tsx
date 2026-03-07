import { useState } from "react";
import { ShoppingBag, Check, AlertCircle } from "lucide-react";

interface AddToCartButtonProps {
  selectedSize: string | null;
  onAddToCart: () => void;
}

type ButtonState = "idle" | "success" | "error";

export default function AddToCartButton({ selectedSize, onAddToCart }: AddToCartButtonProps) {
  const [state, setState] = useState<ButtonState>("idle");

  const handleClick = () => {
    if (!selectedSize) {
      setState("error");
      setTimeout(() => setState("idle"), 2000);
      return;
    }
    onAddToCart();
    setState("success");
    setTimeout(() => setState("idle"), 2500);
  };

  const isSuccess = state === "success";
  const isError = state === "error";

  return (
    <div className="space-y-2">
      <button
        onClick={handleClick}
        className={`group relative flex h-14 w-full items-center justify-center gap-3 overflow-hidden rounded font-display text-lg tracking-widest transition-all duration-300
          ${isSuccess
            ? "bg-green-600 text-white"
            : isError
            ? "bg-brand-surface border-2 border-brand-red text-brand-red animate-pulse"
            : "bg-brand-red text-white hover:bg-brand-red-bright active:scale-[0.98]"
          }`}
      >
        {/* Shimmer on hover */}
        {!isSuccess && !isError && (
          <span className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/10 to-transparent transition-transform duration-700 group-hover:translate-x-full" />
        )}

        {isSuccess ? (
          <>
            <Check size={20} />
            ADDED TO BAG
          </>
        ) : isError ? (
          <>
            <AlertCircle size={20} />
            SELECT A SIZE
          </>
        ) : (
          <>
            <ShoppingBag size={20} />
            ADD TO BAG
          </>
        )}
      </button>

      <button className="flex h-12 w-full items-center justify-center gap-2 rounded border border-border font-body text-sm font-medium text-muted-foreground transition-all duration-200 hover:border-foreground/30 hover:text-foreground">
        ♡ &nbsp;Save to Wishlist
      </button>
    </div>
  );
}
