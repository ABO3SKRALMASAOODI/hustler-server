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
- Write the hook from the payoff: the clip's strongest line or statistic as
  a specific claim the ending completes, never a generic question, and never
  a word a later graphic slams.
- Keep the viewer's eyes moving with purpose: word cues, graphics, B-roll,
  layout — each bound to what is being said, each adding something the
  captions cannot.
- Never bait without payoff; never print the punchline in the hook text.

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
  0 s is an option, never a requirement. No reflexive opening whoosh: a
  title already on screen at frame 0 has no entrance for a sound, and the
  same whoosh on every short is a template. At most one library sound sits
  under the opening, only when the title has a real entrance that earns
  one. The speaker is on screen and talking by ~0.3 s.
- HOOK TEXT by 1.5 s, PAYOFF-LED: write it from the clip's strongest line
  or statistic — a specific third-person claim the ending pays off. A
  lecture that ends on "…all we got was 140 characters" opens on "They
  promised us flying cars…" and lets the ending complete it; a study clip
  leads with its number. Never a generic question ("WHERE DID PROGRESS
  GO?"): a question works only when it names something specific (a number,
  a name, a claim). Set the payoff up without printing its punchline.
- THE HOOK IS A HEADLINE THAT OWNS ITS ZONE: `word_slam` with
  `tier='hook'` sets the *starred* main line at 7%+ of the frame height
  (caps; broken onto two lines when one line would set it smaller) with
  the lead-in small above it — "They promised us / *flying cars*…" — and
  the captions wait until it exits, so the first seconds have one reading
  task. A headline band is the other way. Never hook type no bigger than
  the captions with the live caption fading in under it: two sentences at
  once in the first second.
- NEVER SPEND A LATER HERO WORD: the hook must not show the word a later
  graphic slams. GARBAGE in the hook and the same GARBAGE slam at 13 s makes
  the real moment land as a repeat. Leave the word for the moment it is
  spoken, or transform the later instance (a quote card with attribution
  and year, the evidence on screen).
- COLD OPEN (optional): a 1.5–2 s tease of the punchline before the setup,
  only when the punchline exists as its own clip asset (`insert_media` at
  0); kept footage always plays in source order, so otherwise the
  payoff-led hook text carries the tease.
- NAME THE SPEAKER in the hook's kicker or the headline band (a verified
  name, plus the year on archival footage). A broadcast lower third over a
  famous face is a second text system competing with the hook: skip it.
- FIRST FRAME is the thumbnail: sharp, composed, designed, never black or
  half-faded, and the speaker faces camera (not in profile, not mid-blink).
  The first audio is a clean word onset — never the tail of the previous
  word or a disfluency ("if somebody was like,") — and the hook plays as
  one take: no jump cut inside the first 1.5 s, at most one cut in the
  first 3 s. No fade-in, no logo, no dead air, no non-speaker setup longer
  than ~3 s.
- SAY THE HOOK BACK in the reply: one clause naming what you opened on.

RHYTHM — the middle:
- A visual change every 0.3–0.6 s (motion-caption word reveals count) and
  the structure moving with the speech: a graphic, B-roll evidence or a
  layout shift where the story turns. A camera move only where a specific
  moment calls for one (the payoff word, a real turn) — never to fill time.
- Diagnose dead stretches by missing information, contrast or anticipation:
  a long hold with nothing changing is a design gap, not restraint. The
  thesis line, a spoken list or triad, a named product or place and a
  number each get a beat that ADDS information (a contrast like BITS vs
  ATOMS, an accumulating `list_build`, an identification, the number, an
  image), with no more than ~6–8 s between hero beats in the body — and
  never more than one per 6–8 s. A zoom or a sound is not a beat.
- Vary the interval: cluster changes on dense ideas and lists, let a real
  admission or reaction hold on the face with only captions.
- MIDDLE DISCIPLINE: every sentence earns its place — repetition, hedging,
  throat-clearing, low-information connectors ("it was like a lot of things
  and", "I think that's kind of a tell that") and second takes go, while
  sentence boundaries keep a 150–250 ms breath (cutting owns the
  mechanics). No stretch over ~3 s without a new idea. A 34 s reel that
  keeps moving beats a 58 s reel with the same content.

HERO MOMENTS — 2 to 4 per reel, on exact words: the claim, the number, the
turn, the payoff. Each gets one leader graphic that EARNS ITS PLACE — it
adds what the captions cannot (a number, a contrast, an identification,
evidence, an image), at most about one per 6–8 s, never a re-typeset of
the words being heard (short-form-direction has the budget). A camera move
or a sound on the same frame only when that landing clearly earns it,
within the sparse budget (motion-design has the templates and timing;
zooms and audio the rules for the optional camera and sound).

OPEN LOOPS: a hook that asks ("this mistake cost me $40k") needs its answer
to arrive. Choose when to answer for tension and comprehension; never distort
the speaker's meaning.

THE PAYOFF: the largest accented lockup in the short, the number and its
noun together ("140 / CHARACTERS", never a bare 140), face-safe. Where the
story set up a device earlier, rhyme with it (a 1960s-vs-TODAY split pays
off as FLYING CARS vs 140 CHARACTERS). Hold 0.8–1.5 s after the last word
(a big payoff number about 2 s on screen) before the end card instead of
cutting away on the last syllable; a sound
or a camera move joins only when the landing clearly earns one, and a
reaction button gets 1.0–1.5 s or is left out.

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
- Hook text that prints the payoff, a generic question, or a word a later
  graphic slams; no hook text at all.
- An opening on a word fragment, a disfluency, a jump cut or a profile.
- A payoff number without its noun, or under 0.8 s of air before the end
  card.
- Hook type no bigger than the captions with a live caption under it; a
  9–15 s stretch of body captions over the thesis or a triad.
- Long static stretches between graphics; or changes on a timer that ignore
  what is being said.
- Payoff cut off before it lands; CTA covering the payoff; dead tail.

## Verification procedure

Watch the opening without assumed context and measure: first visual event
time, hook text visible by 1.5 s, speaker on screen and facing camera by
~0.3 s, no cut in the first 1.5 s. Scan the program for holds over ~2 s
without design. Trace each escalation to the payoff, check the payoff hold
(0.8–1.5 s after the last word; ~2 s for a payoff number), the CTA placement and the loop frame in
the complete preview. Read the render's EARN ITS PLACE advisory: it names a
generic or spent hook, a fragment opening and a short payoff hold by time
(advisory: keep if intentional).

## Repair ladder

Move a clearer promise to the front → rewrite the hook from the payoff's
line or statistic → add the interrupt and hook text →
restore minimum setup → fill dead holds with word cues or a graphic → compress
sagging beats → protect and accent the payoff → place the CTA after it →
rescreen from frame one.
