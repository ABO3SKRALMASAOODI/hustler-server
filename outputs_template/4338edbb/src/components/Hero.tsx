const Hero = () => {
  const handleScroll = (href: string) => {
    const el = document.querySelector(href);
    if (el) el.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <section
      id="hero"
      className="relative min-h-screen flex items-center justify-center overflow-hidden"
    >
      {/* Background image */}
      <div
        className="absolute inset-0 bg-cover bg-center bg-no-repeat"
        style={{
          backgroundImage: `url('https://placehold.co/1920x1080/0a0a0a/1a1a1a?text=.')`,
        }}
      >
        {/* Layered overlays for depth */}
        <div className="absolute inset-0 bg-gradient-to-b from-black/70 via-black/40 to-black/80" />
        <div className="absolute inset-0 bg-gradient-to-r from-black/40 via-transparent to-black/40" />
        {/* Subtle red vignette */}
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_transparent_40%,_rgba(180,20,20,0.18)_100%)]" />
      </div>

      {/* Decorative grain texture */}
      <div
        className="absolute inset-0 opacity-[0.035] pointer-events-none"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E")`,
        }}
      />

      {/* Content */}
      <div className="relative z-10 text-center px-6 max-w-4xl mx-auto">
        {/* Eyebrow */}
        <p className="animate-fade-in delay-100 font-body text-xs tracking-[0.4em] uppercase text-gold mb-6">
          Fine Dining · Est. 1997
        </p>

        {/* Ornament line */}
        <div className="animate-fade-in delay-200 flex items-center justify-center gap-4 mb-8">
          <span className="w-12 h-px bg-gradient-to-r from-transparent to-gold/60" />
          <span className="text-gold text-lg">✦</span>
          <span className="w-12 h-px bg-gradient-to-l from-transparent to-gold/60" />
        </div>

        {/* Restaurant name */}
        <h1 className="animate-fade-in-up delay-200 font-display font-black text-6xl sm:text-7xl md:text-8xl lg:text-9xl text-foreground leading-none tracking-tight mb-2">
          EMBER
        </h1>
        <h2 className="animate-fade-in-up delay-300 font-display italic font-light text-3xl sm:text-4xl md:text-5xl text-gold tracking-wide mb-8">
          &amp; Vine
        </h2>

        {/* Tagline */}
        <p className="animate-fade-in-up delay-400 font-body text-base sm:text-lg md:text-xl text-foreground/70 max-w-xl mx-auto leading-relaxed mb-12">
          Where fire meets finesse. Seasonal ingredients,{" "}
          <em className="text-foreground/90 not-italic font-light">
            extraordinary craft
          </em>
          , and an atmosphere that lingers long after the last course.
        </p>

        {/* CTAs */}
        <div className="animate-fade-in-up delay-500 flex flex-col sm:flex-row items-center justify-center gap-4">
          <button
            onClick={() => handleScroll("#reservations")}
            className="group px-8 py-4 bg-crimson text-primary-foreground font-body text-sm tracking-[0.2em] uppercase hover:bg-red-600 transition-all duration-300 rounded-sm shadow-lg shadow-crimson/20 hover:shadow-crimson/40 hover:shadow-xl"
          >
            Reserve a Table
          </button>
          <button
            onClick={() => handleScroll("#menu")}
            className="px-8 py-4 border border-foreground/30 text-foreground font-body text-sm tracking-[0.2em] uppercase hover:border-gold hover:text-gold transition-all duration-300 rounded-sm"
          >
            View Our Menu
          </button>
        </div>

        {/* Hours badge */}
        <p className="animate-fade-in delay-600 mt-10 font-body text-xs tracking-widest uppercase text-muted-foreground">
          Open Tue–Sun · 5:30 PM – 11:00 PM
        </p>
      </div>

      {/* Scroll indicator */}
      <div className="absolute bottom-10 left-1/2 -translate-x-1/2 animate-bounce">
        <div className="w-px h-10 bg-gradient-to-b from-gold/60 to-transparent mx-auto mb-1" />
        <div className="w-1 h-1 bg-gold/60 rounded-full mx-auto" />
      </div>
    </section>
  );
};

export default Hero;
