export interface MenuItem {
  id: string;
  name: string;
  description: string;
  price: string;
  image: string;
  badge?: "Chef's Pick" | "New" | "Vegetarian" | "Signature";
}

export interface MenuCategory {
  id: string;
  label: string;
  items: MenuItem[];
}

export const menuCategories: MenuCategory[] = [
  {
    id: "starters",
    label: "Starters",
    items: [
      {
        id: "s1",
        name: "Seared Scallops",
        description: "Pan-seared diver scallops with cauliflower purée, crispy capers, and brown butter vinaigrette.",
        price: "$22",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Seared+Scallops",
        badge: "Chef's Pick",
      },
      {
        id: "s2",
        name: "Beef Tartare",
        description: "Hand-cut prime tenderloin, dijon aioli, cornichons, shallots, and grilled crostini.",
        price: "$19",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Beef+Tartare",
        badge: "Signature",
      },
      {
        id: "s3",
        name: "Burrata & Heirloom",
        description: "Fresh burrata with heirloom tomatoes, basil oil, Maldon sea salt, and aged balsamic.",
        price: "$17",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Burrata",
        badge: "Vegetarian",
      },
      {
        id: "s4",
        name: "Lobster Bisque",
        description: "Rich Maine lobster bisque with a touch of brandy, crème fraîche, and chive oil.",
        price: "$18",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Lobster+Bisque",
      },
    ],
  },
  {
    id: "mains",
    label: "Main Course",
    items: [
      {
        id: "m1",
        name: "Dry-Aged Ribeye",
        description: "28-day dry-aged 16oz ribeye, roasted bone marrow butter, truffle-parmesan frites, and chimichurri.",
        price: "$68",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Dry-Aged+Ribeye",
        badge: "Signature",
      },
      {
        id: "m2",
        name: "Roasted Duck Breast",
        description: "Magret duck breast, cherry gastrique, celery root gratin, and wilted greens.",
        price: "$48",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Roasted+Duck",
        badge: "Chef's Pick",
      },
      {
        id: "m3",
        name: "Pan-Roasted Halibut",
        description: "Pacific halibut with leek beurre blanc, asparagus, fingerling potatoes, and dill oil.",
        price: "$52",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Pan+Halibut",
      },
      {
        id: "m4",
        name: "Braised Short Rib",
        description: "72-hour braised wagyu short rib, horseradish cream, parsnip purée, and crispy shallots.",
        price: "$58",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Short+Rib",
        badge: "New",
      },
      {
        id: "m5",
        name: "Wild Mushroom Risotto",
        description: "Carnaroli rice, porcini, chanterelle, and shiitake mushrooms, aged Parmigiano, truffle oil.",
        price: "$38",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Risotto",
        badge: "Vegetarian",
      },
      {
        id: "m6",
        name: "Whole Roasted Chicken",
        description: "Free-range chicken, herb jus, confit garlic mashed potato, and roasted root vegetables.",
        price: "$42",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Roasted+Chicken",
      },
    ],
  },
  {
    id: "sides",
    label: "Sides",
    items: [
      {
        id: "si1",
        name: "Truffle Frites",
        description: "Double-fried potatoes tossed with black truffle oil, Parmesan, and fresh chives.",
        price: "$14",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Truffle+Frites",
      },
      {
        id: "si2",
        name: "Grilled Broccolini",
        description: "Charred broccolini with preserved lemon, toasted almonds, and Calabrian chili.",
        price: "$12",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Broccolini",
        badge: "Vegetarian",
      },
      {
        id: "si3",
        name: "Creamed Spinach",
        description: "Wilted spinach in a rich béchamel, with nutmeg and crispy breadcrumbs.",
        price: "$11",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Creamed+Spinach",
        badge: "Vegetarian",
      },
      {
        id: "si4",
        name: "Lobster Mac & Cheese",
        description: "Cavatappi pasta, three-cheese mornay, chunks of Maine lobster, and brioche crumbs.",
        price: "$18",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Lobster+Mac",
        badge: "Signature",
      },
    ],
  },
  {
    id: "desserts",
    label: "Desserts",
    items: [
      {
        id: "d1",
        name: "Valrhona Chocolate Soufflé",
        description: "Warm dark chocolate soufflé with Tahitian vanilla crème anglaise and cocoa nib tuile.",
        price: "$16",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Chocolate+Souffl%C3%A9",
        badge: "Signature",
      },
      {
        id: "d2",
        name: "Crème Brûlée",
        description: "Classic Tahitian vanilla custard, caramelized sugar crust, and fresh raspberries.",
        price: "$13",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Cr%C3%A8me+Br%C3%BCl%C3%A9e",
        badge: "Chef's Pick",
      },
      {
        id: "d3",
        name: "Tarte Tatin",
        description: "Upside-down caramelized apple tart, butter puff pastry, Calvados crème fraîche.",
        price: "$14",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Tarte+Tatin",
      },
      {
        id: "d4",
        name: "Cheese Selection",
        description: "A curated board of seasonal artisan cheeses with honeycomb, walnuts, and fruit preserves.",
        price: "$22",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Cheese+Board",
      },
    ],
  },
  {
    id: "cocktails",
    label: "Cocktails",
    items: [
      {
        id: "c1",
        name: "Ember Old Fashioned",
        description: "Smoked rye whiskey, demerara syrup, Angostura bitters, charred orange zest.",
        price: "$19",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Old+Fashioned",
        badge: "Signature",
      },
      {
        id: "c2",
        name: "Garden Gimlet",
        description: "Hendrick's gin, fresh lime, cucumber, elderflower, and tonic foam.",
        price: "$17",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Garden+Gimlet",
        badge: "New",
      },
      {
        id: "c3",
        name: "Blood & Smoke",
        description: "Mezcal, blood orange, chipotle honey, fresh lime, smoked salt rim.",
        price: "$18",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Blood+%26+Smoke",
        badge: "Chef's Pick",
      },
      {
        id: "c4",
        name: "Champagne Velvet",
        description: "Moët & Chandon, St-Germain, fresh lychee purée, and edible gold flake.",
        price: "$22",
        image: "https://placehold.co/400x300/1a1a1a/c8a96e?text=Champagne+Velvet",
      },
    ],
  },
];
