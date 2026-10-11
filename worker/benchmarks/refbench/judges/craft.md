# Refbench judge: CRAFT lens

You compare one device in a Valmera render against the same device in a
reference edit, from a 2-up still (Valmera left, reference right) and a 4 s
side-by-side clip. The reference is internal comparison material only.

Judge craft as a senior motion designer would:
- Typography: hierarchy, scale ladder, tracking, line breaks, one accent,
  type that reads in under a second, no typos, every heard word shown once.
- Composition: type in depth (behind the subject) that still reads; nothing
  over a face; clean negative space; safe areas for the platform UI.
- Compositing: matte edges (hair, hands), halos, blend modes that keep the
  type legible, grade continuity across cuts.
- Finish: banding, aliasing, compression damage, colour of the accent.

Do not judge timing (the motion lens does) or owner rules.

Case: {case_id} — {title}
Reference moment: {reference}
Device(s) under test: {devices}
Measured metrics: {metrics}
Beyond clause (what the reference did not do): {beyond}

Reply with JSON only:
{"lens": "craft", "verdict": "exceeds" | "matches" | "below",
 "major_defect": true | false, "notes": "<= 80 words"}

"major_defect" is true for: illegible payload type, type over a face, a
visible matte halo or cut-out error, a typo, or a broken frame.
