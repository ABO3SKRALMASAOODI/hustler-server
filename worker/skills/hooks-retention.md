# hooks-retention — the first half-second, the hook line by 1.5 s, a change every 0.3–0.6 s, hero moments, payoff, native CTA, the loop

## Editorial decision principles

Short-form is decided before the thumb moves. The references put a visual
pattern interrupt on screen within 0.1–0.6 s, the hook line as text within
1.5 s, and keep something visual changing every 0.3–0.6 s all the way to a
held payoff and a CTA that plays like native UI. Retention comes from a
clear promise, escalating information and an earned payoff — the design
makes each of those impossible to miss.

- Open on the strongest intelligible line or frame, with design on it from
  frame one.
- Keep the viewer's eyes moving with purpose: word cues, graphics, B-roll,
  layout — each bound to what is being said.
- Never bait without payoff; never spoil the payoff in the hook text.

ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES (owner, Oct 2026):
restraint is the default. Reach for a zoom or a sound only when a specific
moment needs it — a key word, a reveal, a genuinely jarring jump cut, a
real-world action shown — and zero is a fine answer. Never a zoom per cut,
a camera move per hero moment or a sound per landing or transition: used
where nothing calls for them they make an edit look childish.

## Evidence to inspect

Inspect the whole transcript for the strongest hook line, setup
dependencies, energy turns, open questions and the payoff; the first frames
of the program (`look_at(output_times=[0.0, 0.2, 0.6, 1.5])`, then the same
times rendered on a complete preview);
dead holds in the program map; the ending and loop frame.

## Strong treatment patterns

THE HOOK — 0 to 1.5 s:
- FIND it before cutting: the most extreme claim, the boldest visual, the
  question that opens a loop, the mid-action moment ("we lost everything" >
  "hi guys, today…"). It is rarely at the start of the raw footage.
- START on it: kept footage always plays in source order (`keep_segments`
  sorts its spans), so a later line cannot be moved ahead of earlier speech.
  Trim the lead-in so the first kept sentence IS the hook; when the payoff
  line sits later, open on the tension it answers and pose it as hook type.
  Keep the minimum context to be understood.
- PATTERN INTERRUPT within 0.1–0.6 s: a `hook_title` or `word_slam` on
  the first strong word, a card reveal, a flash or light leak. A punch-in at
  0 s is an option, never a requirement, and at most one library sound sits
  under the opening, only when its landing earns one (a soft whoosh into
  the title). The speaker is on screen and talking by ~0.3 s.
- HOOK TEXT by 1.5 s: the hook line (or its sharpest 2–6 words) as designed
  type, posed as the question or tension — never the answer.
- FIRST FRAME is the thumbnail: sharp, composed, designed, never black or
  half-faded. No fade-in, no logo, no dead air, no non-speaker setup longer
  than ~3 s.
- SAY THE HOOK BACK in the reply: one clause naming what you opened on.

RHYTHM — the middle:
- A visual change every 0.3–0.6 s (motion-caption word reveals count) and
  the structure moving with the speech: a graphic, B-roll evidence or a
  layout shift where the story turns. A camera move only where a specific
  moment calls for one (the payoff word, a real turn) — never to fill time.
- Diagnose dead stretches by missing information, contrast or anticipation:
  a long hold with nothing changing is a design gap, not restraint.
- Vary the interval: cluster changes on dense ideas and lists, let a real
  admission or reaction hold on the face with only captions.
- MIDDLE DISCIPLINE: every sentence earns its place — repetition, hedging,
  throat-clearing and second takes go (cutting owns the mechanics). A 34 s
  reel that keeps moving beats a 58 s reel with the same content.

HERO MOMENTS — 2 to 4 per reel, on exact words: the claim, the number, the
turn, the payoff. Each gets one leader graphic; a camera move or a sound on
the same frame only when that landing clearly earns it, within the sparse
budget (motion-design has the templates and timing; zooms and audio the
rules for the optional camera and sound).

OPEN LOOPS: a hook that asks ("this mistake cost me $40k") needs its answer
to arrive. Choose when to answer for tension and comprehension; never distort
the speaker's meaning.

THE PAYOFF: hold it 1.0–1.5 s with an accent — a scale-in, a colour change,
the short's one impact, or the music's button when the user supplied music —
instead of cutting away on the last syllable.

CTA AS NATIVE UI: after the payoff has landed, in the last 2–4 s, use
`comment_cta` (a typed comment keyword), `follow_cta` (follow → following)
or `save_cta` (bookmark fill) — one CTA, never over the payoff, with at most
a click on its visible press (`sfx=true`). Use it only when the user or
brief asks for a CTA, and only with the handle, keyword and offer they
supplied (owner marketing reels supply them in the brief): never invent a
handle, a verified badge, a comment keyword or a promised resource.
`save_cta` needs no identity.

END ON THE LOOP: land the last cut on the last word or beat — no fade, no dead
tail, no "thanks for watching". The best endings rhyme with the first frame
so replay is seamless. Preserve a needed reaction or emotional release rather
than forcing an abrupt cut.

## Common failure modes

- First visual event after 1 s; a silent or black opening; a long setup by
  someone other than the speaker.
- Hook text that prints the payoff; no hook text at all.
- Long static stretches between graphics; or changes on a timer that ignore
  what is being said.
- Payoff cut off before it lands; CTA covering the payoff; dead tail.

## Verification procedure

Watch the opening without assumed context and measure: first visual event
time, hook text visible by 1.5 s, speaker on screen by ~0.3 s. Scan the
program for holds over ~2 s without design. Trace each escalation to the
payoff, check the payoff hold, the CTA placement and the loop frame in the
complete preview.

## Repair ladder

Move a clearer promise to the front → add the interrupt and hook text →
restore minimum setup → fill dead holds with word cues or a graphic → compress
sagging beats → protect and accent the payoff → place the CTA after it →
rescreen from frame one.
