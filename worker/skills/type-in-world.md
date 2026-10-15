# type-in-world — words laid ON real surfaces: the desk, the floor under the speaker's shadow, a wall, a projection or an LED screen, warped into their perspective

## Editorial decision principles

Type on a surface belongs to the room: it has the surface's perspective,
the person's hands and body pass over it, and their real shadow darkens
it. Done well it is the most "impossible" device in a short — the
references that use it were shot for it. Use it when the frame has a
clear surface the words can own, and the line is about a place, a thing on
the table, a plan, a price, a claim the room should carry.

- **Desk / table**: the price, the product's name, the one number — laid
  flat in front of the speaker; their hands cross it.
- **Floor**: big type under a standing creator, under their real shadow
  (`shadow=true`).
- **Wall / projection / LED wall**: a headline printed into the wall
  (`blend: multiply` on bright walls, screen on dark ones); the speaker's
  head passes in front of it.
- **Screen / monitor**: words on the device's screen plane.

Invent: one sentence that walks across surfaces (desk → wall → floor) as
the camera cuts; a word that is printed on the projection and then peels
off into the air; a list that stacks up on the desk one item per beat.
Never the same surface trick twice in a row.

## Evidence to inspect

- `look_at(t)` for the surface's four corners in program fractions: TL,
  TR, BR, BL along the surface (TL→TR runs along its width).
- The surface must be visible for the window and big enough for the words;
  the verb places the type in the largest part of the surface that is on
  screen and not covered by the person when it lands.
- A moving camera is tracked automatically (`planes.h`); a locked-off shot
  is a single homography.

## Strong treatment patterns

- `pin_to_plane(text='$19', quad=[[...],[...],[...],[...]], t=<time>,
  surface='desk')` — writes the plane and the words; the person in front
  occludes desk and floor type by default.
- `pin_to_plane(motion_id='m3', plane='pl1', blend='multiply')` to print an
  existing graphic into a bright wall.
- `shadow=true` on floors and desks in hard light.

## Common failure modes

- A quad that is not on one flat surface (two walls, a wall and a desk):
  the type bends wrongly. One surface per plane.
- Corners in the wrong order: TL→TR must run along the surface's width.
- Type that lands where the person's head will be: the auto place avoids
  where they stand at the landing; a big move later still covers it —
  shorten the window or choose another surface.
- Dark type on a dark desk: pick a colour against the surface (or blend
  screen).

## Verification procedure

- Preview and check: the corners sit on the surface's edges (the plane is
  refined to the nearest straight edges), the type does not swim (a
  locked-off shot never moves; a tracked one stays within ~1.5 px at
  1080p), hands and body pass over it cleanly.
- Notes "plane tracking not measured yet: pinned to its first frame" or
  "shadow transfer off" in the render report mean a preview fallback.

## Repair ladder

1. Wrong surface edges: give a tighter quad from look_at.
2. Type too small: `place` a bigger part of the plane, or fewer words.
3. Swimming on a moving camera: shorten the window to one shot.
4. Not measured: re-render; or keep the words screen-locked and say so.

## Through apply_edit_batch

```json
{"action": "upsert", "layer": "fx", "id": "pl1", "value": {"id": "pl1",
 "type": "plane", "stage": "world", "start": 2.2, "end": 5.0,
 "src_t": 28.6, "surface": "screen",
 "quad": [[0.266, 0.148], [0.792, 0.096], [0.773, 0.43], [0.242, 0.467]],
 "device": "plane_type"}}
{"action": "upsert", "layer": "motion", "id": "cone", "value": {
 "id": "cone", "anchor": {"to": "plane:pl1", "occluders": ["person"]},
 "composite": {"blend": "multiply"}}}
```

The quad is SOURCE fractions at SOURCE second `src_t`. `place` [u0, v0,
u1, v1] picks a part of the plane; `shadow` true transfers real shadows.
