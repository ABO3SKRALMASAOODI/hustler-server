import { useEffect, useState } from "react";
import { ArrowDown, Github, Linkedin, Twitter, Download } from "lucide-react";

const TITLES = [
  "Full-Stack Developer",
  "React Specialist",
  "Node.js Engineer",
  "UI/UX Craftsman",
];

const HeroSection = () => {
  const [titleIndex, setTitleIndex] = useState(0);
  const [displayed, setDisplayed] = useState("");
  const [isDeleting, setIsDeleting] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    const current = TITLES[titleIndex];
    const delay = isDeleting ? 50 : 100;

    const timeout = setTimeout(() => {
      if (!isDeleting) {
        setDisplayed(current.slice(0, displayed.length + 1));
        if (displayed.length + 1 === current.length) {
          setTimeout(() => setIsDeleting(true), 1800);
        }
      } else {
        setDisplayed(current.slice(0, displayed.length - 1));
        if (displayed.length === 0) {
          setIsDeleting(false);
          setTitleIndex((prev) => (prev + 1) % TITLES.length);
        }
      }
    }, delay);

    return () => clearTimeout(timeout);
  }, [displayed, isDeleting, titleIndex]);

  const scrollToSection = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <section
      id="hero"
      className="relative min-h-screen flex flex-col justify-center overflow-hidden grid-bg"
    >
      {/* Red accent bar top */}
      <div className="absolute top-0 left-0 right-0 h-1 bg-crimson" />

      {/* Radial glow */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-crimson/5 rounded-full blur-[100px]" />
      </div>

      {/* Decorative corner */}
      <div className="absolute top-24 right-8 md:right-24 w-32 h-32 border-t-2 border-r-2 border-crimson/30 hidden md:block" />
      <div className="absolute bottom-24 left-8 md:left-24 w-24 h-24 border-b-2 border-l-2 border-crimson/20 hidden md:block" />

      {/* Number decorations */}
      <span className="absolute left-6 top-1/2 -translate-y-1/2 font-grotesk text-[8rem] font-bold text-white/[0.02] select-none hidden lg:block leading-none">
        01
      </span>

      <div className="max-w-6xl mx-auto px-6 pt-28 pb-20 w-full">
        <div className="grid lg:grid-cols-2 gap-12 items-center">
          {/* Left — Text */}
          <div>
            {/* Badge */}
            <div
              className={`inline-flex items-center gap-2 mb-6 transition-all duration-700 delay-100 ${
                mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4"
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
              <span className="font-inter text-xs text-muted-foreground tracking-widest uppercase">
                Available for work
              </span>
            </div>

            {/* Name */}
            <h1
              className={`font-grotesk text-5xl md:text-7xl font-bold leading-none mb-4 transition-all duration-700 delay-200 ${
                mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"
              }`}
            >
              Alex
              <br />
              <span className="text-crimson">Morgan</span>
            </h1>

            {/* Animated title */}
            <div
              className={`flex items-center gap-0 mb-8 h-10 transition-all duration-700 delay-300 ${
                mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"
              }`}
            >
              <span className="font-grotesk text-xl md:text-2xl text-muted-foreground font-medium">
                {displayed}
              </span>
              <span className="w-0.5 h-7 bg-crimson ml-1 cursor-blink" />
            </div>

            {/* Description */}
            <p
              className={`font-inter text-muted-foreground text-base md:text-lg leading-relaxed max-w-md mb-10 transition-all duration-700 delay-[400ms] ${
                mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"
              }`}
            >
              I build performant, scalable web applications with a keen eye for
              design. 5+ years of turning complex problems into elegant digital
              solutions.
            </p>

            {/* CTAs */}
            <div
              className={`flex flex-wrap gap-4 mb-12 transition-all duration-700 delay-500 ${
                mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"
              }`}
            >
              <button
                onClick={() => scrollToSection("projects")}
                className="font-grotesk font-semibold px-8 py-3.5 bg-crimson text-white hover:bg-crimson/90 transition-all duration-200 glow-red-sm hover:glow-red clip-corner"
              >
                View My Work
              </button>
              <button
                onClick={() => scrollToSection("contact")}
                className="font-grotesk font-semibold px-8 py-3.5 border border-border text-foreground hover:border-crimson hover:text-crimson transition-all duration-200 flex items-center gap-2"
              >
                <Download size={16} />
                Download CV
              </button>
            </div>

            {/* Socials */}
            <div
              className={`flex items-center gap-5 transition-all duration-700 delay-[600ms] ${
                mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"
              }`}
            >
              <span className="font-inter text-xs text-muted-foreground tracking-widest uppercase">
                Find me on
              </span>
              <div className="flex gap-4">
                {[
                  { icon: Github, href: "https://github.com", label: "GitHub" },
                  { icon: Linkedin, href: "https://linkedin.com", label: "LinkedIn" },
                  { icon: Twitter, href: "https://twitter.com", label: "Twitter" },
                ].map(({ icon: Icon, href, label }) => (
                  <a
                    key={label}
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={label}
                    className="w-9 h-9 border border-border flex items-center justify-center text-muted-foreground hover:border-crimson hover:text-crimson transition-all duration-200"
                  >
                    <Icon size={15} />
                  </a>
                ))}
              </div>
            </div>
          </div>

          {/* Right — Avatar card */}
          <div
            className={`hidden lg:flex justify-center items-center transition-all duration-1000 delay-300 ${
              mounted ? "opacity-100 scale-100" : "opacity-0 scale-95"
            }`}
          >
            <div className="relative">
              {/* Decorative rings */}
              <div className="absolute -inset-6 border border-crimson/15 rounded-full" />
              <div className="absolute -inset-12 border border-crimson/8 rounded-full" />

              {/* Photo card */}
              <div className="relative w-72 h-80 clip-corner overflow-hidden border-2 border-crimson/50">
                <img
                  src="https://placehold.co/288x320/0a0a0a/e8182a?text=AM"
                  alt="Alex Morgan"
                  className="w-full h-full object-cover"
                />
                {/* Overlay gradient */}
                <div className="absolute inset-0 bg-gradient-to-t from-background/60 via-transparent to-transparent" />
                {/* Stats badge */}
                <div className="absolute bottom-4 left-4 right-4 bg-surface/90 backdrop-blur border border-border p-3">
                  <div className="flex justify-between">
                    {[
                      { value: "5+", label: "Years Exp" },
                      { value: "40+", label: "Projects" },
                      { value: "25+", label: "Clients" },
                    ].map((stat) => (
                      <div key={stat.label} className="text-center">
                        <div className="font-grotesk font-bold text-lg text-crimson leading-none">
                          {stat.value}
                        </div>
                        <div className="font-inter text-[10px] text-muted-foreground mt-0.5">
                          {stat.label}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Tech badges floating */}
              <div className="absolute -top-3 -right-6 bg-surface border border-border px-3 py-1.5 font-grotesk text-xs font-semibold text-crimson">
                React
              </div>
              <div className="absolute -bottom-3 -left-6 bg-surface border border-border px-3 py-1.5 font-grotesk text-xs font-semibold text-crimson">
                Node.js
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Scroll indicator */}
      <button
        onClick={() => scrollToSection("about")}
        className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2 text-muted-foreground hover:text-crimson transition-colors"
        aria-label="Scroll down"
      >
        <span className="font-inter text-xs tracking-widest uppercase">Scroll</span>
        <ArrowDown size={16} className="animate-bounce" />
      </button>
    </section>
  );
};

export default HeroSection;
