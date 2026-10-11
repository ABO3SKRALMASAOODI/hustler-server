# House corpus (HC) shot list

For the owner or team: one session, about 1.5–2 hours, one phone on a
tripod. These shots unblock tests that no library footage can (ROADMAP §4.3).
Until they exist, each test runs on the stand-in named below and is marked
`interim: true` in its refbench case.

Everything here is **owned footage**, so it is also the preferred material
for any public demo of genres A, B, D and F (ROADMAP §9, owner decision 7).

## Before you start

- **Camera.** Phone on a tripod, locked exposure and focus (tap-and-hold
  AE/AF lock), 1× lens unless a shot says otherwise.
- **Formats.** 4K at 30 fps for everything. Where a shot says **60p**, shoot
  1080p at 60 fps as well (a second take is fine).
- **Light.** Daylight or one soft key. Avoid flicker: no cheap LED panels at
  odd shutter speeds. If a shot needs a hard shadow, use one bare source.
- **Sound.** Leave the phone mic on (room tone is useful). Voice reads (HC-I)
  need a close mic: a lavalier or a USB mic 15–20 cm from the mouth.
- **Clean plates.** Where a shot says "clean plate", hold the empty frame
  for **5 seconds** before anyone enters and after they leave. Do not move
  the camera between the plate and the action.
- **Slate.** Say the shot ID aloud at the start of each take ("H C A, take
  two"). It makes the files easy to sort.
- **Wardrobe.** A plain dark top for most shots; the saturated red jacket for
  HC-D only. No logos or brand marks on anything in frame.
- **People.** Anyone on camera agrees in writing that the footage may be used
  in Valmera tests and public demos.

## The shots

| ID | Setup | What to film (each 10–30 s unless noted) | Unblocks | Interim stand-in |
|---|---|---|---|---|
| **HC-A** | Camera overhead (tripod arm or a high shelf), pointing straight down at a plain floor. One bare light low to one side so the person casts a long, hard shadow. | 1. Clean plate, 5 s. 2. Person walks into frame, stops, gestures, walks out; locked camera. 3. Same action, camera handheld (mild sway). 4. Clean plate again, 5 s. | 16@0.08–2.79 (type on the floor under the real shadow); shadow transfer (3C) | none (planes are tested on V05, V06, V12) |
| **HC-B** | Desk, top-down and 45°. Laptop, notebook, pen, mouse, phone, keys. **60p** on every take. | 1. Eye opening, extreme close-up (2× lens). 2. Pen writing one word. 3. Mouse click, macro. 4. Phone unlock (screen content blank or Valmera). 5. Page turn. 6. Keys picked up. 7. Overhead: laptop lid closing slowly. | 08, 15, 19, 26, 29 (macro montages, eye-open recall, match cuts) | V07 4K virtual macros |
| **HC-C** | Talking-head MCU against a plain wall, chest up. | 1. Both hands make a "finger frame" toward the lens and hold it 3 s, three times. 2. Point at the lens, then at the top-left and top-right of frame. 3. Hold a phone up to the camera, screen facing the lens, 5 s. | 25 (effect window bounded by the hands), 21 (fingertip-tracked cards) | V12 two-hand gestures |
| **HC-D** | Saturated **red jacket** on a grey or concrete exterior (overcast is ideal). | 1. Wide: walk toward camera from 10 m to 2 m. 2. Medium: turn and look past the camera. 3. Close-up: head and shoulders, 10 s. | 40 (B&W world with the red kept) | V04 hue-keep + V01 subject isolate |
| **HC-E** | One fixed framing you can return to (a window seat, a doorway). Mark the tripod feet with tape. | The **same** framing three times: day, dusk, night. Same person, same pose, 15 s each. | 04 (same framing, different day), 12 (day/night match) | V04 vs V14 (same set, two days) |
| **HC-F** | A doorway or a wall edge; a table. | 1. Walk behind the door jamb (left to right) so the jamb hides the body for a moment. 2. Hold a wall calendar page up toward the lens, then lower it. 3. Toss a small object (an apple, a lime) across the frame into a hand. | 13 (occlusion by door), 16@14.6 (calendar occludes type), 07@41.7 (thrown object reveals words) | V06 boom mic, V02 laptop/bottle, V03 lectern mic |
| **HC-G** | Turntable (or a lazy Susan) on a seamless background; then a table top shot straight down. | 1. One product (a phone, a mug) rotating one full turn, 20 s. 2. Flat-lay of 8 objects, evenly spaced, 10 s; then remove them one by one. | 01 (product strobe), 30 (flat-lay cut-outs) | V13 Optimus and GPU boards |
| **HC-H** | Locked framing, plain room. | 1. Two takes of the **same** framing: the person on the left in take 1, on the right in take 2 (for a twin clone). 2. The person walks straight into the lens until the body fills the frame (a body wipe). **60p** too. | 22@≈6.0–7.3 (twin), 11@13.93 (body wipe) | V11 / V12 |
| **HC-I** | Close-miked voice reads, dry room (a closet full of clothes works). Script below. | 1. Manifesto, 15 s. 2. Explainer, 30 s. 3. Essay, 45 s. Two takes each; leave 2 s of silence before and after. | 04, 11, 34, 03 (VO-led genres; syllable and rhythm tests) | V01 / V02 speech |

### HC-I reading scripts

Owned words, written for the tests. Read them naturally; do not rush.

- **Manifesto (≈15 s).** "They told us the future was finished. That the
  big ideas were behind us. They were wrong. Every tool we build is a
  promise. Every promise is a beginning. Begin."
- **Explainer (≈30 s).** "Valmera edits from a sentence. You upload the
  footage, say what you want, and it cuts the story, sets the type, and
  measures every frame it touches. If a word lands late, it knows. If the
  type is hard to read, it knows. And when you change the song, every cut
  lands again on the new beat."
- **Essay (≈45 s).** "Look at the light in this room. It moves. Slowly in
  the morning, quickly at dusk. A good edit moves the same way. Long, calm
  holds when the thought is new. Faster cuts when the idea takes hold.
  Silence, just before the point. And then the point. Everything else is
  decoration. Most of it can go."

## Music fixtures (not a shoot)

HM1–HM3 (ROADMAP §4.4) must be **supplied or picked by the owner**: a
140–170 BPM trap/phonk track with a pre-drop gap and a drop (HM1), a
cinematic bed with silence then a drop (HM2), and a song with vocals plus
its lyrics (HM3). Engineers and agents never choose them. Store each with
`provenance=user_upload` (or `user_picked_library`).

## Delivery

Copy the files into `~/Documents/Valmera/.valmera/fixtures/HC/<ID>/` (never
into git), named `<ID>_<take>.mov`. Then run

```
python -m benchmarks.refbench vfl build     # after adding HC windows to vfl.json
```

and the HC-required tests (3B-4, 3C-1) and the interim tests switch to the
real footage on their next run.
