import { useScrollAnimation } from "@/hooks/useScrollAnimation";
import SectionHeading from "./SectionHeading";

interface Skill {
  name: string;
  level: number;
}

interface SkillCategory {
  category: string;
  icon: string;
  skills: Skill[];
}

const SKILL_DATA: SkillCategory[] = [
  {
    category: "Frontend",
    icon: "⬡",
    skills: [
      { name: "React / Next.js", level: 95 },
      { name: "TypeScript", level: 90 },
      { name: "Tailwind CSS", level: 92 },
      { name: "Framer Motion", level: 78 },
    ],
  },
  {
    category: "Backend",
    icon: "⬡",
    skills: [
      { name: "Node.js / Express", level: 88 },
      { name: "PostgreSQL", level: 82 },
      { name: "GraphQL", level: 75 },
      { name: "REST APIs", level: 93 },
    ],
  },
  {
    category: "DevOps & Tools",
    icon: "⬡",
    skills: [
      { name: "Docker", level: 80 },
      { name: "AWS / Vercel", level: 78 },
      { name: "Git / CI-CD", level: 91 },
      { name: "Linux", level: 76 },
    ],
  },
];

const TECH_BADGES = [
  "React", "Next.js", "TypeScript", "JavaScript", "Node.js", "Express",
  "PostgreSQL", "MongoDB", "GraphQL", "Prisma", "Docker", "AWS",
  "Tailwind CSS", "Figma", "Git", "Vitest", "Redis", "tRPC",
];

interface SkillBarProps {
  name: string;
  level: number;
  delay: number;
  parentVisible: boolean;
}

const SkillBar = ({ name, level, delay, parentVisible }: SkillBarProps) => (
  <div className="mb-5">
    <div className="flex justify-between items-center mb-1.5">
      <span className="font-inter text-sm text-foreground/80">{name}</span>
      <span className="font-grotesk text-xs font-bold text-crimson">{level}%</span>
    </div>
    <div className="h-1 bg-border rounded-full overflow-hidden">
      <div
        className="h-full bg-crimson rounded-full transition-all duration-1000 ease-out"
        style={{
          width: parentVisible ? `${level}%` : "0%",
          transitionDelay: `${delay}ms`,
        }}
      />
    </div>
  </div>
);

interface CategoryCardProps {
  data: SkillCategory;
  index: number;
}

const CategoryCard = ({ data, index }: CategoryCardProps) => {
  const { ref, isVisible } = useScrollAnimation<HTMLDivElement>({ threshold: 0.2 });

  return (
    <div
      ref={ref}
      className={`bg-surface border border-border p-6 transition-all duration-700 hover:border-crimson/40 group ${
        isVisible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-8"
      }`}
      style={{ transitionDelay: `${index * 120}ms` }}
    >
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <span className="text-crimson text-xl leading-none">{data.icon}</span>
        <h3 className="font-grotesk text-lg font-bold text-foreground">
          {data.category}
        </h3>
        <div className="ml-auto w-6 h-0.5 bg-crimson/40 group-hover:bg-crimson transition-colors duration-300" />
      </div>

      {/* Bars */}
      <div>
        {data.skills.map((skill, i) => (
          <SkillBar
            key={skill.name}
            name={skill.name}
            level={skill.level}
            delay={i * 80 + index * 120}
            parentVisible={isVisible}
          />
        ))}
      </div>
    </div>
  );
};

const SkillsSection = () => {
  const { ref: badgesRef, isVisible: badgesVisible } = useScrollAnimation();

  return (
    <section id="skills" className="py-28 px-6 relative">
      {/* Decorative number */}
      <span className="absolute right-6 top-20 font-grotesk text-[8rem] font-bold text-white/[0.02] select-none hidden lg:block leading-none">
        02
      </span>

      <div className="max-w-6xl mx-auto">
        <SectionHeading
          label="Expertise"
          title="My //Skills"
          subtitle="A breakdown of technologies I work with daily, from frontend architecture to cloud deployments."
        />

        {/* Skill cards */}
        <div className="grid md:grid-cols-3 gap-6 mb-16">
          {SKILL_DATA.map((cat, i) => (
            <CategoryCard key={cat.category} data={cat} index={i} />
          ))}
        </div>

        {/* Tech badge cloud */}
        <div
          ref={badgesRef}
          className={`transition-all duration-700 ${
            badgesVisible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"
          }`}
        >
          <p className="font-inter text-xs text-muted-foreground tracking-widest uppercase mb-6">
            Also proficient with
          </p>
          <div className="flex flex-wrap gap-3">
            {TECH_BADGES.map((tech, i) => (
              <span
                key={tech}
                className="font-grotesk text-sm px-4 py-2 border border-border text-muted-foreground hover:border-crimson hover:text-crimson transition-all duration-200 cursor-default"
                style={{ transitionDelay: `${i * 30}ms` }}
              >
                {tech}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

export default SkillsSection;
