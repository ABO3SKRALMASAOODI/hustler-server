import { useState } from "react";
import { ExternalLink, Github, ArrowRight } from "lucide-react";
import { useScrollAnimation } from "@/hooks/useScrollAnimation";
import SectionHeading from "./SectionHeading";

interface Project {
  id: number;
  title: string;
  description: string;
  tags: string[];
  image: string;
  liveUrl: string;
  repoUrl: string;
  featured: boolean;
  category: string;
}

const PROJECTS: Project[] = [
  {
    id: 1,
    title: "Vaultify — Finance Dashboard",
    description:
      "A real-time personal finance tracker with AI-powered insights, budget management, and multi-currency support. Built for 10k+ monthly active users.",
    tags: ["React", "TypeScript", "Node.js", "PostgreSQL", "Recharts"],
    image: "https://placehold.co/600x380/0d0d0d/e8182a?text=Vaultify",
    liveUrl: "https://example.com",
    repoUrl: "https://github.com",
    featured: true,
    category: "Full-Stack",
  },
  {
    id: 2,
    title: "Nexus CMS — Headless Platform",
    description:
      "Headless CMS with a drag-and-drop editor, custom field types, and a GraphQL API. Powers 50+ client websites.",
    tags: ["Next.js", "GraphQL", "Prisma", "tRPC", "Tailwind"],
    image: "https://placehold.co/600x380/0d0d0d/e8182a?text=Nexus+CMS",
    liveUrl: "https://example.com",
    repoUrl: "https://github.com",
    featured: true,
    category: "Full-Stack",
  },
  {
    id: 3,
    title: "Pulse — Dev Productivity App",
    description:
      "A keyboard-driven task manager for developers with GitHub integration, sprint planning, and focus timers.",
    tags: ["React", "Electron", "SQLite", "GitHub API"],
    image: "https://placehold.co/600x380/0d0d0d/e8182a?text=Pulse",
    liveUrl: "https://example.com",
    repoUrl: "https://github.com",
    featured: false,
    category: "Desktop",
  },
  {
    id: 4,
    title: "ShipRoute — Logistics API",
    description:
      "RESTful microservices architecture for real-time shipment tracking, driver assignment, and route optimization.",
    tags: ["Node.js", "Express", "MongoDB", "Redis", "Docker"],
    image: "https://placehold.co/600x380/0d0d0d/e8182a?text=ShipRoute",
    liveUrl: "https://example.com",
    repoUrl: "https://github.com",
    featured: false,
    category: "Backend",
  },
  {
    id: 5,
    title: "Helios UI — Component Library",
    description:
      "An accessible, themeable React component library with 40+ components, full TypeScript support, and Storybook documentation.",
    tags: ["React", "TypeScript", "Storybook", "CSS-in-JS", "Vitest"],
    image: "https://placehold.co/600x380/0d0d0d/e8182a?text=Helios+UI",
    liveUrl: "https://example.com",
    repoUrl: "https://github.com",
    featured: false,
    category: "Frontend",
  },
  {
    id: 6,
    title: "MeshNet — WebRTC Chat App",
    description:
      "Peer-to-peer encrypted video and text chat with room management, screen sharing, and end-to-end encryption.",
    tags: ["React", "WebRTC", "Socket.io", "Node.js", "WebCrypto"],
    image: "https://placehold.co/600x380/0d0d0d/e8182a?text=MeshNet",
    liveUrl: "https://example.com",
    repoUrl: "https://github.com",
    featured: false,
    category: "Full-Stack",
  },
];

const FILTERS = ["All", "Full-Stack", "Frontend", "Backend", "Desktop"];

interface ProjectCardProps {
  project: Project;
  index: number;
}

const ProjectCard = ({ project, index }: ProjectCardProps) => {
  const { ref, isVisible } = useScrollAnimation<HTMLDivElement>({ threshold: 0.1 });
  const [hovered, setHovered] = useState(false);

  return (
    <div
      ref={ref}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={`group bg-surface border border-border hover:border-crimson/50 transition-all duration-500 flex flex-col overflow-hidden ${
        isVisible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-10"
      }`}
      style={{ transitionDelay: `${(index % 3) * 100}ms` }}
    >
      {/* Image */}
      <div className="relative overflow-hidden aspect-video">
        <img
          src={project.image}
          alt={project.title}
          className={`w-full h-full object-cover transition-transform duration-700 ${
            hovered ? "scale-105" : "scale-100"
          }`}
        />
        <div className="absolute inset-0 bg-gradient-to-t from-surface via-surface/20 to-transparent" />

        {/* Featured badge */}
        {project.featured && (
          <div className="absolute top-3 left-3 bg-crimson text-white font-grotesk text-[10px] font-semibold tracking-widest uppercase px-2 py-1">
            Featured
          </div>
        )}

        {/* Category */}
        <div className="absolute top-3 right-3 bg-surface/80 backdrop-blur border border-border font-grotesk text-[10px] font-semibold tracking-widest uppercase px-2 py-1 text-muted-foreground">
          {project.category}
        </div>

        {/* Action buttons on hover */}
        <div
          className={`absolute inset-0 flex items-center justify-center gap-4 bg-background/70 backdrop-blur-sm transition-opacity duration-300 ${
            hovered ? "opacity-100" : "opacity-0"
          }`}
        >
          <a
            href={project.liveUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 bg-crimson text-white font-grotesk text-sm font-semibold px-5 py-2.5 hover:bg-crimson/90 transition-colors"
            onClick={(e) => e.stopPropagation()}
          >
            <ExternalLink size={14} />
            Live Demo
          </a>
          <a
            href={project.repoUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 border border-white/30 text-white font-grotesk text-sm font-semibold px-5 py-2.5 hover:border-white/70 transition-colors"
            onClick={(e) => e.stopPropagation()}
          >
            <Github size={14} />
            Code
          </a>
        </div>
      </div>

      {/* Content */}
      <div className="p-5 flex flex-col flex-1">
        <h3 className="font-grotesk text-base font-bold text-foreground mb-2 group-hover:text-crimson transition-colors duration-200">
          {project.title}
        </h3>
        <p className="font-inter text-sm text-muted-foreground leading-relaxed mb-4 flex-1">
          {project.description}
        </p>

        {/* Tags */}
        <div className="flex flex-wrap gap-1.5 mb-4">
          {project.tags.map((tag) => (
            <span
              key={tag}
              className="font-grotesk text-[11px] px-2 py-0.5 bg-muted text-muted-foreground border border-border"
            >
              {tag}
            </span>
          ))}
        </div>

        {/* Links row */}
        <div className="flex items-center justify-between pt-3 border-t border-border">
          <a
            href={project.liveUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 font-grotesk text-xs font-semibold text-crimson hover:underline"
          >
            View Project <ArrowRight size={12} />
          </a>
          <a
            href={project.repoUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-muted-foreground hover:text-foreground transition-colors"
            aria-label="GitHub repo"
          >
            <Github size={14} />
          </a>
        </div>
      </div>
    </div>
  );
};

const ProjectsSection = () => {
  const [activeFilter, setActiveFilter] = useState("All");

  const filtered = PROJECTS.filter(
    (p) => activeFilter === "All" || p.category === activeFilter
  );

  return (
    <section id="projects" className="py-28 px-6 relative bg-surface/50">
      {/* Decorative number */}
      <span className="absolute left-6 top-20 font-grotesk text-[8rem] font-bold text-white/[0.02] select-none hidden lg:block leading-none">
        03
      </span>

      <div className="max-w-6xl mx-auto">
        <SectionHeading
          label="Work"
          title="Featured //Projects"
          subtitle="A selection of projects I've built — from SaaS platforms to open-source tools and everything in between."
        />

        {/* Filter tabs */}
        <div className="flex flex-wrap gap-2 mb-10">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setActiveFilter(f)}
              className={`font-grotesk text-sm font-semibold px-5 py-2 border transition-all duration-200 ${
                activeFilter === f
                  ? "border-crimson bg-crimson text-white"
                  : "border-border text-muted-foreground hover:border-crimson/50 hover:text-foreground"
              }`}
            >
              {f}
            </button>
          ))}
        </div>

        {/* Grid */}
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
          {filtered.map((project, i) => (
            <ProjectCard key={project.id} project={project} index={i} />
          ))}
        </div>

        {/* CTA */}
        <div className="mt-12 text-center">
          <a
            href="https://github.com"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 font-grotesk text-sm font-semibold text-muted-foreground hover:text-crimson transition-colors border-b border-muted-foreground hover:border-crimson pb-0.5"
          >
            <Github size={15} />
            See all repositories on GitHub
            <ArrowRight size={13} />
          </a>
        </div>
      </div>
    </section>
  );
};

export default ProjectsSection;
