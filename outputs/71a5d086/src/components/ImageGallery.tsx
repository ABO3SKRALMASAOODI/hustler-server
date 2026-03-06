import { useState } from "react";
import { ChevronLeft, ChevronRight, ZoomIn } from "lucide-react";

interface ImageGalleryProps {
  images: string[];
  productName: string;
}

export default function ImageGallery({ images, productName }: ImageGalleryProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [zoomed, setZoomed] = useState(false);

  const prev = () => setActiveIndex((i) => (i === 0 ? images.length - 1 : i - 1));
  const next = () => setActiveIndex((i) => (i === images.length - 1 ? 0 : i + 1));

  return (
    <div className="flex flex-col gap-4">
      {/* Main Image */}
      <div className="relative overflow-hidden rounded-lg bg-brand-surface group aspect-square">
        <img
          key={activeIndex}
          src={images[activeIndex]}
          alt={`${productName} — view ${activeIndex + 1}`}
          className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105 animate-scale-in"
        />

        {/* Zoom hint */}
        <button
          onClick={() => setZoomed(!zoomed)}
          className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-full bg-background/70 text-muted-foreground opacity-0 backdrop-blur-sm transition-all duration-200 group-hover:opacity-100 hover:bg-brand-red hover:text-white"
        >
          <ZoomIn size={15} />
        </button>

        {/* Prev / Next */}
        <button
          onClick={prev}
          className="absolute left-3 top-1/2 -translate-y-1/2 flex h-9 w-9 items-center justify-center rounded-full bg-background/70 text-foreground backdrop-blur-sm transition-all duration-200 hover:bg-brand-red hover:text-white opacity-0 group-hover:opacity-100"
        >
          <ChevronLeft size={18} />
        </button>
        <button
          onClick={next}
          className="absolute right-3 top-1/2 -translate-y-1/2 flex h-9 w-9 items-center justify-center rounded-full bg-background/70 text-foreground backdrop-blur-sm transition-all duration-200 hover:bg-brand-red hover:text-white opacity-0 group-hover:opacity-100"
        >
          <ChevronRight size={18} />
        </button>

        {/* Dot Indicators */}
        <div className="absolute bottom-3 left-1/2 flex -translate-x-1/2 gap-1.5">
          {images.map((_, i) => (
            <button
              key={i}
              onClick={() => setActiveIndex(i)}
              className={`h-1.5 rounded-full transition-all duration-300 ${
                i === activeIndex
                  ? "w-6 bg-brand-red"
                  : "w-1.5 bg-foreground/30 hover:bg-foreground/60"
              }`}
            />
          ))}
        </div>
      </div>

      {/* Thumbnails */}
      <div className="grid grid-cols-5 gap-2">
        {images.map((src, i) => (
          <button
            key={i}
            onClick={() => setActiveIndex(i)}
            className={`relative overflow-hidden rounded aspect-square transition-all duration-200 ${
              i === activeIndex
                ? "ring-2 ring-brand-red ring-offset-2 ring-offset-background"
                : "opacity-50 hover:opacity-80"
            }`}
          >
            <img
              src={src}
              alt={`Thumbnail ${i + 1}`}
              className="h-full w-full object-cover"
            />
          </button>
        ))}
      </div>
    </div>
  );
}
