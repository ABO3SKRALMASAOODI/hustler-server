import Navbar from "@/components/Navbar";
import HeroSection from "@/components/HeroSection";
import SkillsSection from "@/components/SkillsSection";
import ProjectsSection from "@/components/ProjectsSection";
import ContactSection from "@/components/ContactSection";
import { Github, Linkedin, Twitter, Mail } from "lucide-react";

const Footer = () => (
  <footer className="border-t border-border py-10 px-6">
    <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
      <div className="font-grotesk text-sm text-muted-foreground">
        <span className="text-crimson font-bold">&lt;Alex Morgan /&gt;</span>
        {" "}— Built with React & Tailwind CSS
      </div>
      <div className="flex items-center gap-4">
        {[
          { icon: Github, href: "https://github.com", label: "GitHub" },
          { icon: Linkedin, href: "https://linkedin.com", label: "LinkedIn" },
          { icon: Twitter, href: "https://twitter.com", label: "Twitter" },
          { icon: Mail, href: "mailto:alex@morgan.dev", label: "Email" },
        ].map(({ icon: Icon, href, label }) => (
          <a
            key={label}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={label}
            className="text-muted-foreground hover:text-crimson transition-colors"
          >
            <Icon size={16} />
          </a>
        ))}
      </div>
      <div className="font-inter text-xs text-muted-foreground">
        © {new Date().getFullYear()} Alex Morgan. All rights reserved.
      </div>
    </div>
  </footer>
);

const AboutSection = () => {
  return (
    <section id="about" className="py-28 px-6 bg-surface/30 relative overflow-hidden">
      {/* Decorative line */}
      <div className="absolute left-0 top-0 bottom-0 w-px bg-gradient-to-b from-transparent via-crimson/20 to-transparent hidden lg:block" />

      <div className="max-w-6xl mx-auto grid lg:grid-cols-2 gap-16 items-center">
        {/* Left — quote block */}
        <div>
          <span className="inline-block font-grotesk text-xs font-semibold tracking-[0.3em] uppercase text-crimson mb-3">
            About Me
          </span>
          <h2 className="font-grotesk text-4xl md:text-5xl font-bold text-foreground leading-tight mb-6">
            Crafting Code
            <br />
            <span className="text-crimson">With Purpose</span>
          </h2>
          <div className="space-y-4 font-inter text-muted-foreground leading-relaxed">
            <p>
              I'm a full-stack developer based in San Francisco with 5+ years of experience
              building products that live at the intersection of great engineering and thoughtful
              design. I care deeply about performance, accessibility, and developer experience.
            </p>
            <p>
              My background spans early-stage startups to enterprise scale — I've led frontend
              architecture, shipped mobile apps, and built data pipelines that process millions
              of events per day.
            </p>
            <p>
              When I'm not writing code, you'll find me contributing to open source, writing
              about web performance on my blog, or exploring the California coastline.
            </p>
          </div>

          <div className="mt-8 grid grid-cols-2 gap-6">
            {[
              { value: "5+", label: "Years of Experience" },
              { value: "40+", label: "Projects Shipped" },
              { value: "25+", label: "Happy Clients" },
              { value: "12k+", label: "GitHub Stars" },
            ].map((stat) => (
              <div key={stat.label} className="border-l-2 border-crimson pl-4">
                <div className="font-grotesk text-3xl font-bold text-foreground">{stat.value}</div>
                <div className="font-inter text-xs text-muted-foreground mt-0.5">{stat.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Right — experience timeline */}
        <div>
          <p className="font-inter text-xs text-muted-foreground uppercase tracking-widest mb-8">
            Work Experience
          </p>
          <div className="relative space-y-0">
            {[
              {
                year: "2022 — Present",
                role: "Senior Frontend Engineer",
                company: "Stripe",
                desc: "Leading the developer dashboard redesign serving 2M+ developers.",
              },
              {
                year: "2020 — 2022",
                role: "Full-Stack Developer",
                company: "Linear",
                desc: "Built core issue-tracking features and real-time collaboration engine.",
              },
              {
                year: "2018 — 2020",
                role: "Software Engineer",
                company: "Vercel",
                desc: "Worked on the deployment pipeline and edge network infrastructure.",
              },
            ].map((exp, i) => (
              <div key={i} className="flex gap-6 pb-8 relative">
                {/* Timeline line */}
                <div className="flex flex-col items-center pt-1.5">
                  <div className="w-2.5 h-2.5 rounded-full bg-crimson shrink-0 relative z-10" />
                  {i < 2 && <div className="w-px flex-1 bg-border mt-1" />}
                </div>
                {/* Content */}
                <div className="flex-1 pb-2">
                  <span className="font-grotesk text-xs text-crimson font-semibold">{exp.year}</span>
                  <h4 className="font-grotesk font-bold text-foreground mt-0.5">{exp.role}</h4>
                  <p className="font-grotesk text-sm text-crimson/70 mb-1">@ {exp.company}</p>
                  <p className="font-inter text-sm text-muted-foreground">{exp.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

const Index = () => {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <Navbar />
      <HeroSection />
      <AboutSection />
      <SkillsSection />
      <ProjectsSection />
      <ContactSection />
      <Footer />
    </div>
  );
};

export default Index;
