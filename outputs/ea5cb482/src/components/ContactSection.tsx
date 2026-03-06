import { useState, FormEvent } from "react";
import { Mail, MapPin, Clock, Send, Github, Linkedin, Twitter, CheckCircle } from "lucide-react";
import { useScrollAnimation } from "@/hooks/useScrollAnimation";
import SectionHeading from "./SectionHeading";

interface FormState {
  name: string;
  email: string;
  subject: string;
  message: string;
}

const INITIAL_FORM: FormState = { name: "", email: "", subject: "", message: "" };

const CONTACT_INFO = [
  {
    icon: Mail,
    label: "Email",
    value: "alex@morgan.dev",
    href: "mailto:alex@morgan.dev",
  },
  {
    icon: MapPin,
    label: "Location",
    value: "San Francisco, CA",
    href: null,
  },
  {
    icon: Clock,
    label: "Response Time",
    value: "Within 24 hours",
    href: null,
  },
];

const SOCIALS = [
  { icon: Github, label: "GitHub", href: "https://github.com", handle: "@alexmorgan" },
  { icon: Linkedin, label: "LinkedIn", href: "https://linkedin.com", handle: "in/alexmorgan" },
  { icon: Twitter, label: "Twitter / X", href: "https://twitter.com", handle: "@alexmorgan_dev" },
];

const ContactSection = () => {
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [errors, setErrors] = useState<Partial<FormState>>({});
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);

  const { ref: leftRef, isVisible: leftVisible } = useScrollAnimation();
  const { ref: rightRef, isVisible: rightVisible } = useScrollAnimation();

  const validate = (): boolean => {
    const newErrors: Partial<FormState> = {};
    if (!form.name.trim()) newErrors.name = "Name is required";
    if (!form.email.trim()) newErrors.email = "Email is required";
    else if (!/\S+@\S+\.\S+/.test(form.email)) newErrors.email = "Invalid email address";
    if (!form.subject.trim()) newErrors.subject = "Subject is required";
    if (!form.message.trim()) newErrors.message = "Message is required";
    else if (form.message.trim().length < 20) newErrors.message = "Message must be at least 20 characters";
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    setLoading(true);
    // Simulate API call
    setTimeout(() => {
      setLoading(false);
      setSubmitted(true);
      setForm(INITIAL_FORM);
    }, 1800);
  };

  const handleChange = (field: keyof FormState, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    if (errors[field]) setErrors((prev) => ({ ...prev, [field]: undefined }));
  };

  return (
    <section id="contact" className="py-28 px-6 relative">
      {/* Decorative number */}
      <span className="absolute right-6 top-20 font-grotesk text-[8rem] font-bold text-white/[0.02] select-none hidden lg:block leading-none">
        04
      </span>

      {/* Red accent bottom bar */}
      <div className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-crimson via-crimson/50 to-transparent" />

      <div className="max-w-6xl mx-auto">
        <SectionHeading
          label="Get In Touch"
          title="Let's //Work Together"
          subtitle="Have a project in mind or want to collaborate? I'd love to hear from you."
        />

        <div className="grid lg:grid-cols-5 gap-12">
          {/* Left — Info */}
          <div
            ref={leftRef}
            className={`lg:col-span-2 transition-all duration-700 ${
              leftVisible ? "opacity-100 translate-x-0" : "opacity-0 -translate-x-8"
            }`}
          >
            <p className="font-inter text-muted-foreground leading-relaxed mb-10">
              I'm currently available for freelance projects, full-time roles, and open-source
              collaborations. Whether it's a quick question or a long-term engagement — my inbox
              is always open.
            </p>

            {/* Contact details */}
            <div className="space-y-5 mb-10">
              {CONTACT_INFO.map(({ icon: Icon, label, value, href }) => (
                <div key={label} className="flex items-center gap-4">
                  <div className="w-10 h-10 border border-crimson/30 flex items-center justify-center text-crimson shrink-0">
                    <Icon size={16} />
                  </div>
                  <div>
                    <div className="font-inter text-xs text-muted-foreground uppercase tracking-widest mb-0.5">
                      {label}
                    </div>
                    {href ? (
                      <a href={href} className="font-grotesk text-sm font-medium text-foreground hover:text-crimson transition-colors">
                        {value}
                      </a>
                    ) : (
                      <span className="font-grotesk text-sm font-medium text-foreground">{value}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Social links */}
            <div>
              <p className="font-inter text-xs text-muted-foreground uppercase tracking-widest mb-4">
                Connect with me
              </p>
              <div className="space-y-3">
                {SOCIALS.map(({ icon: Icon, label, href, handle }) => (
                  <a
                    key={label}
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-3 group"
                  >
                    <div className="w-8 h-8 border border-border flex items-center justify-center text-muted-foreground group-hover:border-crimson group-hover:text-crimson transition-all duration-200">
                      <Icon size={14} />
                    </div>
                    <div>
                      <div className="font-grotesk text-xs font-semibold text-foreground group-hover:text-crimson transition-colors">
                        {label}
                      </div>
                      <div className="font-inter text-xs text-muted-foreground">{handle}</div>
                    </div>
                  </a>
                ))}
              </div>
            </div>
          </div>

          {/* Right — Form */}
          <div
            ref={rightRef}
            className={`lg:col-span-3 transition-all duration-700 delay-200 ${
              rightVisible ? "opacity-100 translate-x-0" : "opacity-0 translate-x-8"
            }`}
          >
            {submitted ? (
              <div className="bg-surface border border-crimson/40 p-10 flex flex-col items-center justify-center gap-5 min-h-[400px] text-center">
                <div className="w-16 h-16 rounded-full border-2 border-crimson flex items-center justify-center">
                  <CheckCircle size={32} className="text-crimson" />
                </div>
                <h3 className="font-grotesk text-2xl font-bold text-foreground">Message Sent!</h3>
                <p className="font-inter text-muted-foreground max-w-xs">
                  Thanks for reaching out, Alex. I'll get back to you within 24 hours.
                </p>
                <button
                  onClick={() => setSubmitted(false)}
                  className="mt-2 font-grotesk text-sm font-semibold px-6 py-2.5 border border-crimson text-crimson hover:bg-crimson hover:text-white transition-all duration-200"
                >
                  Send Another
                </button>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="bg-surface border border-border p-8 space-y-5">
                <div className="grid sm:grid-cols-2 gap-5">
                  {/* Name */}
                  <div>
                    <label className="block font-inter text-xs text-muted-foreground uppercase tracking-widest mb-2">
                      Name *
                    </label>
                    <input
                      type="text"
                      value={form.name}
                      onChange={(e) => handleChange("name", e.target.value)}
                      placeholder="John Doe"
                      className={`w-full bg-background border px-4 py-3 font-inter text-sm text-foreground placeholder-muted-foreground focus:outline-none transition-colors ${
                        errors.name ? "border-red-500" : "border-border focus:border-crimson"
                      }`}
                    />
                    {errors.name && (
                      <p className="mt-1 font-inter text-xs text-red-500">{errors.name}</p>
                    )}
                  </div>

                  {/* Email */}
                  <div>
                    <label className="block font-inter text-xs text-muted-foreground uppercase tracking-widest mb-2">
                      Email *
                    </label>
                    <input
                      type="email"
                      value={form.email}
                      onChange={(e) => handleChange("email", e.target.value)}
                      placeholder="john@example.com"
                      className={`w-full bg-background border px-4 py-3 font-inter text-sm text-foreground placeholder-muted-foreground focus:outline-none transition-colors ${
                        errors.email ? "border-red-500" : "border-border focus:border-crimson"
                      }`}
                    />
                    {errors.email && (
                      <p className="mt-1 font-inter text-xs text-red-500">{errors.email}</p>
                    )}
                  </div>
                </div>

                {/* Subject */}
                <div>
                  <label className="block font-inter text-xs text-muted-foreground uppercase tracking-widest mb-2">
                    Subject *
                  </label>
                  <input
                    type="text"
                    value={form.subject}
                    onChange={(e) => handleChange("subject", e.target.value)}
                    placeholder="Project inquiry / Collaboration"
                    className={`w-full bg-background border px-4 py-3 font-inter text-sm text-foreground placeholder-muted-foreground focus:outline-none transition-colors ${
                      errors.subject ? "border-red-500" : "border-border focus:border-crimson"
                    }`}
                  />
                  {errors.subject && (
                    <p className="mt-1 font-inter text-xs text-red-500">{errors.subject}</p>
                  )}
                </div>

                {/* Message */}
                <div>
                  <label className="block font-inter text-xs text-muted-foreground uppercase tracking-widest mb-2">
                    Message *
                  </label>
                  <textarea
                    value={form.message}
                    onChange={(e) => handleChange("message", e.target.value)}
                    rows={5}
                    placeholder="Tell me about your project..."
                    className={`w-full bg-background border px-4 py-3 font-inter text-sm text-foreground placeholder-muted-foreground focus:outline-none transition-colors resize-none ${
                      errors.message ? "border-red-500" : "border-border focus:border-crimson"
                    }`}
                  />
                  {errors.message && (
                    <p className="mt-1 font-inter text-xs text-red-500">{errors.message}</p>
                  )}
                </div>

                {/* Submit */}
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full flex items-center justify-center gap-3 font-grotesk font-bold text-sm py-4 bg-crimson text-white hover:bg-crimson/90 disabled:opacity-60 disabled:cursor-not-allowed transition-all duration-200 glow-red-sm hover:glow-red"
                >
                  {loading ? (
                    <>
                      <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Sending...
                    </>
                  ) : (
                    <>
                      <Send size={15} />
                      Send Message
                    </>
                  )}
                </button>
              </form>
            )}
          </div>
        </div>
      </div>
    </section>
  );
};

export default ContactSection;
