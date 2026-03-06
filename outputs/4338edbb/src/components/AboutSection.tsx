import { Award, Leaf, Flame } from "lucide-react";

const stats = [
  { value: "27", label: "Years of Excellence" },
  { value: "3", label: "James Beard Nominations" },
  { value: "4.9", label: "Average Guest Rating" },
];

const pillars = [
  {
    icon: Flame,
    title: "Fire & Craft",
    description:
      "Our open wood-fire hearth sits at the heart of the kitchen — a 700°F canvas where proteins caramelize and flavors deepen beyond what gas or induction can achieve.",
  },
  {
    icon: Leaf,
    title: "Seasonal Sourcing",
    description:
      "We partner with 14 regional farms within 150 miles. Our menu changes weekly, guided by what is most alive, most vibrant, and most honest in each season.",
  },
  {
    icon: Award,
    title: "Artisan Cellar",
    description:
      "Our sommelier-curated wine list spans over 400 labels across six continents, with a deep focus on small-production natural wines and rare Burgundy verticals.",
  },
];

const AboutSection = () => {
  return (
    <section id="about" className="py-24 md:py-32 bg-charcoal">
      <div className="container mx-auto px-6">
        {/* Section header */}
        <div className="text-center mb-20">
          <p className="font-body text-xs tracking-[0.4em] uppercase text-gold mb-4">
            Our Story
          </p>
          <h2 className="font-display font-bold text-4xl sm:text-5xl md:text-6xl text-foreground">
            The Soul of Ember &amp; Vine
          </h2>
          <div className="ornament-divider max-w-xs mx-auto mt-6">
            <span className="text-gold text-base px-2">✦</span>
          </div>
        </div>

        {/* Two-column story layout */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-center mb-24">
          {/* Image */}
          <div className="relative">
            <div className="relative overflow-hidden rounded-sm aspect-[4/5]">
              <img
                src="https://placehold.co/700x875/111111/c8a96e?text=Chef+Marcus+Cole"
                alt="Chef Marcus Cole"
                className="w-full h-full object-cover"
              />
              {/* Red accent border */}
              <div className="absolute inset-0 border border-crimson/20 rounded-sm pointer-events-none" />
            </div>
            {/* Floating name card */}
            <div className="absolute -bottom-6 -right-6 bg-background border border-border p-5 shadow-xl">
              <p className="font-display font-bold text-xl text-foreground">Chef Marcus Cole</p>
              <p className="font-body text-xs tracking-widest uppercase text-gold mt-1">
                Executive Chef & Co-Founder
              </p>
            </div>
            {/* Decorative corner element */}
            <div className="absolute -top-4 -left-4 w-16 h-16 border-t-2 border-l-2 border-gold/40" />
          </div>

          {/* Story text */}
          <div className="lg:pl-6">
            <blockquote className="font-display italic text-2xl md:text-3xl text-foreground/90 leading-relaxed mb-8 border-l-2 border-crimson pl-6">
              "Food is memory made edible. I cook to remind people of something
              they haven't tasted yet."
            </blockquote>
            <div className="space-y-5 font-body text-muted-foreground leading-relaxed">
              <p>
                Ember &amp; Vine was born in 1997 from a single, stubborn belief:
                that a great meal is an act of generosity. Marcus Cole, a
                Culinary Institute of America graduate who trained under Joël
                Robuchon in Paris, returned home to New York with one mission —
                to build a table worth gathering around.
              </p>
              <p>
                Over nearly three decades, that table has hosted everyone from
                first dates to farewell dinners, anniversaries to after-show
                celebrations. The restaurant has evolved, but its soul remains
                unchanged: exceptional ingredients treated with restraint,
                technical precision worn lightly, and hospitality that never
                feels like a performance.
              </p>
              <p>
                Today, Chef Cole leads a kitchen of 22, mentors the next
                generation of cooks, and still personally approves every dish
                before it leaves the pass.
              </p>
            </div>

            {/* Stats row */}
            <div className="mt-10 grid grid-cols-3 gap-6 pt-8 border-t border-border">
              {stats.map((stat) => (
                <div key={stat.label} className="text-center">
                  <div className="font-display font-black text-3xl text-crimson">
                    {stat.value}
                  </div>
                  <div className="font-body text-xs tracking-wide text-muted-foreground mt-1 leading-tight">
                    {stat.label}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Three pillars */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          {pillars.map(({ icon: Icon, title, description }) => (
            <div
              key={title}
              className="group bg-surface border border-border hover:border-gold/30 p-8 rounded-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-lg hover:shadow-black/30"
            >
              <div className="w-12 h-12 flex items-center justify-center bg-crimson/10 border border-crimson/20 rounded-sm mb-5 group-hover:bg-crimson/20 transition-colors duration-300">
                <Icon size={20} className="text-crimson" />
              </div>
              <h3 className="font-display font-semibold text-xl text-foreground mb-3">
                {title}
              </h3>
              <p className="font-body text-sm text-muted-foreground leading-relaxed">
                {description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default AboutSection;
