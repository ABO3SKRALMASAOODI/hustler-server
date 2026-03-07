export interface Product {
  id: string;
  name: string;
  brand: string;
  price: number;
  originalPrice?: number;
  rating: number;
  reviewCount: number;
  description: string;
  details: string[];
  images: string[];
  sizes: Size[];
  badge?: string;
  category: string;
  colorway: string;
}

export interface Size {
  label: string;
  available: boolean;
}

export const mainProduct: Product = {
  id: "SNK-001",
  name: "Phantom Velocity X",
  brand: "VORTEK",
  price: 189,
  originalPrice: 240,
  rating: 4.8,
  reviewCount: 312,
  description:
    "Engineered for those who refuse to slow down. The Phantom Velocity X combines an ultra-responsive carbon-fiber midsole with a breathable upper that molds to your foot. Whether you're on the track, the street, or the stage — you arrive first.",
  details: [
    "Carbon-fiber reinforced midsole for peak energy return",
    "Engineered mesh upper with heat-bonded overlays",
    "Asymmetric lacing system for a secure lockdown fit",
    "Rubber outsole with aggressive traction pattern",
    "Reflective detailing for low-light visibility",
    "Removable OrthoSoft™ insole",
  ],
  images: [
    "https://placehold.co/800x800/111111/e01a2b?text=VORTEK+PVX",
    "https://placehold.co/800x800/0d0d0d/ffffff?text=Side+Profile",
    "https://placehold.co/800x800/111111/e01a2b?text=Sole+Detail",
    "https://placehold.co/800x800/0d0d0d/ffffff?text=Upper+Close-Up",
    "https://placehold.co/800x800/111111/e01a2b?text=Heel+Counter",
  ],
  sizes: [
    { label: "7", available: true },
    { label: "7.5", available: true },
    { label: "8", available: true },
    { label: "8.5", available: false },
    { label: "9", available: true },
    { label: "9.5", available: true },
    { label: "10", available: true },
    { label: "10.5", available: false },
    { label: "11", available: true },
    { label: "11.5", available: true },
    { label: "12", available: true },
    { label: "13", available: false },
  ],
  badge: "SALE",
  category: "Performance Running",
  colorway: "Midnight / Signal Red",
};

export const relatedProducts: Product[] = [
  {
    id: "SNK-002",
    name: "Obsidian Strike",
    brand: "VORTEK",
    price: 210,
    rating: 4.6,
    reviewCount: 198,
    description: "Street-ready silhouette with an aggressive stance.",
    details: [],
    images: ["https://placehold.co/600x600/0d0d0d/e01a2b?text=OBSIDIAN+STRIKE"],
    sizes: [],
    category: "Lifestyle",
    colorway: "Obsidian / White",
  },
  {
    id: "SNK-003",
    name: "Apex Shadow Low",
    brand: "VORTEK",
    price: 155,
    originalPrice: 195,
    rating: 4.5,
    reviewCount: 267,
    description: "Refined low-top built for all-day comfort.",
    details: [],
    images: ["https://placehold.co/600x600/111111/ffffff?text=APEX+SHADOW"],
    sizes: [],
    badge: "SALE",
    category: "Lifestyle",
    colorway: "Graphite / Crimson",
  },
  {
    id: "SNK-004",
    name: "Pulse Reactor Elite",
    brand: "VORTEK",
    price: 249,
    rating: 4.9,
    reviewCount: 89,
    description: "Top-tier performance shoe for elite athletes.",
    details: [],
    images: ["https://placehold.co/600x600/0d0d0d/e01a2b?text=PULSE+REACTOR"],
    sizes: [],
    badge: "NEW",
    category: "Performance",
    colorway: "Black / Signal Red",
  },
  {
    id: "SNK-005",
    name: "Stealth Trainer II",
    brand: "VORTEK",
    price: 175,
    rating: 4.7,
    reviewCount: 144,
    description: "Cross-training beast with a minimalist aesthetic.",
    details: [],
    images: ["https://placehold.co/600x600/111111/ffffff?text=STEALTH+TRAINER"],
    sizes: [],
    category: "Training",
    colorway: "Void Black / Ice",
  },
];
