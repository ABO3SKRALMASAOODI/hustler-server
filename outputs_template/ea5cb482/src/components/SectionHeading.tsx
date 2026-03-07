import { useScrollAnimation } from "@/hooks/useScrollAnimation";

interface SectionHeadingProps {
  label: string;
  title: string;
  subtitle?: string;
  align?: "left" | "center";
}

const SectionHeading = ({ label, title, subtitle, align = "left" }: SectionHeadingProps) => {
  const { ref, isVisible } = useScrollAnimation();

  return (
    <div
      ref={ref}
      className={`mb-16 transition-all duration-700 ${
        isVisible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"
      } ${align === "center" ? "text-center" : "text-left"}`}
    >
      <span className="inline-block font-grotesk text-xs font-semibold tracking-[0.3em] uppercase text-crimson mb-3">
        {label}
      </span>
      <h2 className="font-grotesk text-4xl md:text-5xl font-bold text-foreground leading-tight">
        {title.split("//").map((part, i) =>
          i === 0 ? (
            <span key={i}>{part}</span>
          ) : (
            <span key={i} className="text-crimson">
              //{part}
            </span>
          )
        )}
      </h2>
      {subtitle && (
        <p className="mt-4 text-muted-foreground font-inter text-base md:text-lg max-w-xl leading-relaxed">
          {subtitle}
        </p>
      )}
      <div
        className={`mt-6 h-px bg-gradient-to-r from-crimson via-crimson/30 to-transparent ${
          align === "center" ? "mx-auto max-w-xs" : "max-w-xs"
        }`}
      />
    </div>
  );
};

export default SectionHeading;
