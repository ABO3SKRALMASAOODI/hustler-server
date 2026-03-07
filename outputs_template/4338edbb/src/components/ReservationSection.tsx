import { useState, FormEvent } from "react";
import { CheckCircle, Clock, MapPin, Phone } from "lucide-react";

interface FormState {
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  date: string;
  time: string;
  partySize: string;
  occasion: string;
  notes: string;
}

const timeSlots = [
  "5:30 PM", "6:00 PM", "6:30 PM", "7:00 PM",
  "7:30 PM", "8:00 PM", "8:30 PM", "9:00 PM",
  "9:30 PM", "10:00 PM",
];

const partySizes = ["1", "2", "3", "4", "5", "6", "7", "8", "9-12 (Group)"];
const occasions = [
  "None", "Birthday", "Anniversary", "Business Dinner",
  "Proposal", "Date Night", "Celebration", "Other",
];

const initialForm: FormState = {
  firstName: "",
  lastName: "",
  email: "",
  phone: "",
  date: "",
  time: "",
  partySize: "",
  occasion: "None",
  notes: "",
};

const ReservationSection = () => {
  const [form, setForm] = useState<FormState>(initialForm);
  const [submitted, setSubmitted] = useState(false);
  const [errors, setErrors] = useState<Partial<FormState>>({});

  const validate = (): boolean => {
    const newErrors: Partial<FormState> = {};
    if (!form.firstName.trim()) newErrors.firstName = "Required";
    if (!form.lastName.trim()) newErrors.lastName = "Required";
    if (!form.email.trim() || !/\S+@\S+\.\S+/.test(form.email))
      newErrors.email = "Valid email required";
    if (!form.phone.trim()) newErrors.phone = "Required";
    if (!form.date) newErrors.date = "Required";
    if (!form.time) newErrors.time = "Required";
    if (!form.partySize) newErrors.partySize = "Required";
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>
  ) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
    if (errors[name as keyof FormState]) {
      setErrors((prev) => ({ ...prev, [name]: undefined }));
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (validate()) {
      setSubmitted(true);
    }
  };

  const inputClass = (field: keyof FormState) =>
    `w-full bg-surface border ${
      errors[field] ? "border-crimson" : "border-border"
    } text-foreground font-body text-sm px-4 py-3 rounded-sm focus:outline-none focus:border-gold/60 transition-colors duration-200 placeholder:text-muted-foreground/50`;

  const labelClass = "block font-body text-xs tracking-widest uppercase text-muted-foreground mb-1.5";

  const today = new Date().toISOString().split("T")[0];

  return (
    <section id="reservations" className="py-24 md:py-32 bg-background">
      <div className="container mx-auto px-6">
        {/* Section header */}
        <div className="text-center mb-16">
          <p className="font-body text-xs tracking-[0.4em] uppercase text-gold mb-4">
            Join Us
          </p>
          <h2 className="font-display font-bold text-4xl sm:text-5xl md:text-6xl text-foreground mb-6">
            Reserve Your Table
          </h2>
          <div className="ornament-divider max-w-xs mx-auto">
            <span className="text-gold text-base px-2">✦</span>
          </div>
          <p className="mt-6 font-body text-muted-foreground max-w-lg mx-auto leading-relaxed">
            Reserve online for parties up to 8. For larger groups or private
            dining, please call us directly.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-12 max-w-6xl mx-auto">
          {/* Info sidebar */}
          <div className="space-y-8">
            <div>
              <h3 className="font-display font-semibold text-xl text-foreground mb-5">
                Visit Us
              </h3>
              <div className="space-y-5">
                <div className="flex gap-4">
                  <div className="w-9 h-9 flex-shrink-0 flex items-center justify-center bg-crimson/10 border border-crimson/20 rounded-sm">
                    <Clock size={16} className="text-crimson" />
                  </div>
                  <div>
                    <p className="font-body text-sm font-bold text-foreground">Hours</p>
                    <p className="font-body text-sm text-muted-foreground mt-0.5">
                      Tuesday – Sunday
                      <br />
                      5:30 PM – 11:00 PM
                    </p>
                    <p className="font-body text-xs text-muted-foreground/60 mt-1">
                      Closed Mondays
                    </p>
                  </div>
                </div>
                <div className="flex gap-4">
                  <div className="w-9 h-9 flex-shrink-0 flex items-center justify-center bg-crimson/10 border border-crimson/20 rounded-sm">
                    <MapPin size={16} className="text-crimson" />
                  </div>
                  <div>
                    <p className="font-body text-sm font-bold text-foreground">Location</p>
                    <p className="font-body text-sm text-muted-foreground mt-0.5">
                      42 West 17th Street
                      <br />
                      New York, NY 10011
                    </p>
                  </div>
                </div>
                <div className="flex gap-4">
                  <div className="w-9 h-9 flex-shrink-0 flex items-center justify-center bg-crimson/10 border border-crimson/20 rounded-sm">
                    <Phone size={16} className="text-crimson" />
                  </div>
                  <div>
                    <p className="font-body text-sm font-bold text-foreground">Phone</p>
                    <p className="font-body text-sm text-muted-foreground mt-0.5">
                      +1 (212) 555-0174
                    </p>
                    <p className="font-body text-xs text-muted-foreground/60 mt-1">
                      For large parties & private events
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* Private dining callout */}
            <div className="bg-charcoal border border-gold/20 rounded-sm p-6">
              <h4 className="font-display font-semibold text-foreground mb-2">
                Private Dining
              </h4>
              <p className="font-body text-sm text-muted-foreground leading-relaxed mb-4">
                Our private salon seats up to 28 guests. Perfect for corporate
                events, milestone celebrations, and intimate gatherings.
              </p>
              <a
                href="mailto:private@embervine.com"
                className="font-body text-xs tracking-widest uppercase text-gold hover:text-foreground transition-colors duration-200"
              >
                Inquire Now →
              </a>
            </div>
          </div>

          {/* Form */}
          <div className="lg:col-span-2">
            {submitted ? (
              <div className="flex flex-col items-center justify-center h-full min-h-[400px] bg-charcoal border border-border rounded-sm p-12 text-center">
                <CheckCircle size={52} className="text-gold mb-6" />
                <h3 className="font-display font-bold text-2xl text-foreground mb-3">
                  Reservation Confirmed
                </h3>
                <p className="font-body text-muted-foreground leading-relaxed max-w-sm mb-2">
                  Thank you, {form.firstName}. We've reserved a table for{" "}
                  <strong className="text-foreground">{form.partySize}</strong> on{" "}
                  <strong className="text-foreground">{form.date}</strong> at{" "}
                  <strong className="text-foreground">{form.time}</strong>.
                </p>
                <p className="font-body text-sm text-muted-foreground/70 mb-8">
                  A confirmation has been sent to {form.email}.
                </p>
                <button
                  onClick={() => { setSubmitted(false); setForm(initialForm); }}
                  className="px-6 py-3 border border-border text-foreground font-body text-xs tracking-widest uppercase hover:border-gold hover:text-gold transition-colors duration-200 rounded-sm"
                >
                  Make Another Reservation
                </button>
              </div>
            ) : (
              <form
                onSubmit={handleSubmit}
                className="bg-charcoal border border-border rounded-sm p-8 space-y-6"
                noValidate
              >
                {/* Name row */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                  <div>
                    <label htmlFor="firstName" className={labelClass}>First Name</label>
                    <input
                      id="firstName"
                      name="firstName"
                      type="text"
                      placeholder="Marcus"
                      value={form.firstName}
                      onChange={handleChange}
                      className={inputClass("firstName")}
                    />
                    {errors.firstName && (
                      <p className="text-crimson text-xs mt-1 font-body">{errors.firstName}</p>
                    )}
                  </div>
                  <div>
                    <label htmlFor="lastName" className={labelClass}>Last Name</label>
                    <input
                      id="lastName"
                      name="lastName"
                      type="text"
                      placeholder="Cole"
                      value={form.lastName}
                      onChange={handleChange}
                      className={inputClass("lastName")}
                    />
                    {errors.lastName && (
                      <p className="text-crimson text-xs mt-1 font-body">{errors.lastName}</p>
                    )}
                  </div>
                </div>

                {/* Contact row */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                  <div>
                    <label htmlFor="email" className={labelClass}>Email</label>
                    <input
                      id="email"
                      name="email"
                      type="email"
                      placeholder="you@example.com"
                      value={form.email}
                      onChange={handleChange}
                      className={inputClass("email")}
                    />
                    {errors.email && (
                      <p className="text-crimson text-xs mt-1 font-body">{errors.email}</p>
                    )}
                  </div>
                  <div>
                    <label htmlFor="phone" className={labelClass}>Phone</label>
                    <input
                      id="phone"
                      name="phone"
                      type="tel"
                      placeholder="+1 (212) 555-0000"
                      value={form.phone}
                      onChange={handleChange}
                      className={inputClass("phone")}
                    />
                    {errors.phone && (
                      <p className="text-crimson text-xs mt-1 font-body">{errors.phone}</p>
                    )}
                  </div>
                </div>

                {/* Date / Time / Party */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
                  <div>
                    <label htmlFor="date" className={labelClass}>Date</label>
                    <input
                      id="date"
                      name="date"
                      type="date"
                      min={today}
                      value={form.date}
                      onChange={handleChange}
                      className={inputClass("date")}
                    />
                    {errors.date && (
                      <p className="text-crimson text-xs mt-1 font-body">{errors.date}</p>
                    )}
                  </div>
                  <div>
                    <label htmlFor="time" className={labelClass}>Time</label>
                    <select
                      id="time"
                      name="time"
                      value={form.time}
                      onChange={handleChange}
                      className={inputClass("time")}
                    >
                      <option value="">Select time</option>
                      {timeSlots.map((t) => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                    {errors.time && (
                      <p className="text-crimson text-xs mt-1 font-body">{errors.time}</p>
                    )}
                  </div>
                  <div>
                    <label htmlFor="partySize" className={labelClass}>Party Size</label>
                    <select
                      id="partySize"
                      name="partySize"
                      value={form.partySize}
                      onChange={handleChange}
                      className={inputClass("partySize")}
                    >
                      <option value="">Guests</option>
                      {partySizes.map((p) => (
                        <option key={p} value={p}>{p} {parseInt(p) === 1 ? "Guest" : "Guests"}</option>
                      ))}
                    </select>
                    {errors.partySize && (
                      <p className="text-crimson text-xs mt-1 font-body">{errors.partySize}</p>
                    )}
                  </div>
                </div>

                {/* Occasion */}
                <div>
                  <label htmlFor="occasion" className={labelClass}>Special Occasion</label>
                  <select
                    id="occasion"
                    name="occasion"
                    value={form.occasion}
                    onChange={handleChange}
                    className={inputClass("occasion")}
                  >
                    {occasions.map((o) => (
                      <option key={o} value={o}>{o}</option>
                    ))}
                  </select>
                </div>

                {/* Notes */}
                <div>
                  <label htmlFor="notes" className={labelClass}>
                    Special Requests <span className="text-muted-foreground/40 normal-case tracking-normal">(optional)</span>
                  </label>
                  <textarea
                    id="notes"
                    name="notes"
                    rows={3}
                    placeholder="Dietary restrictions, seating preferences, allergies…"
                    value={form.notes}
                    onChange={handleChange}
                    className={`${inputClass("notes")} resize-none`}
                  />
                </div>

                {/* Submit */}
                <button
                  type="submit"
                  className="w-full py-4 bg-crimson text-primary-foreground font-body text-sm tracking-[0.2em] uppercase hover:bg-red-600 transition-all duration-300 rounded-sm shadow-lg shadow-crimson/20 hover:shadow-crimson/30 hover:shadow-xl"
                >
                  Confirm Reservation
                </button>

                <p className="text-center font-body text-xs text-muted-foreground/60">
                  By reserving, you agree to our cancellation policy. 48-hour notice required for cancellations.
                </p>
              </form>
            )}
          </div>
        </div>
      </div>
    </section>
  );
};

export default ReservationSection;
