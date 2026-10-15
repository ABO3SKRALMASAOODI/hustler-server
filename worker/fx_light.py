"""Light devices on the person matte: the ``light`` fx type (Wave 3, 3D).

ROADMAP Wave 3 / 3D (``fx_light.py``: light trails via matte + lagfun +
dblur); contract in devices/INTERFACES_W3.md §3.3.

  trail     the subject's moving silhouette leaves light: the cut-out,
            tinted, through lagfun (each frame keeps ``decay`` of the last)
            and a short directional blur, screened over the picture
  dissolve  the figure becomes light: from ``start`` its glow and exposure
            climb for 60% of ``duration_s``, then the figure itself fades
            into a soft blur of the room behind it while the light dies —
            pair it with an el_particles "embers" layer emitted from the
            subject's edge (the 41@25-32 move)
  rim       a rim light traced round the subject's edge, screened

Built on track 1B's subject emitter (subject_fx._Emit: the program matte
through request_matte, window-local layers, the soft working height for
blooms), so every composite stays bbox/window-local and frame-exact, and
the program outside the window is untouched (md5). Substitute rung
(no matte): ``halation`` — a soft global bloom over the window, never a
full-frame flash.
"""

import fxgraph
import fxprims
import subject_fx

FILTERS = subject_fx.FILTERS + ("lagfun", "gblur", "blend", "geq",
                                "negate", "maskedmerge")
KEEPS_FRAME_TAGS = True
DISSOLVE_PEAK = 0.6          # share of duration_s at which the light peaks
TRAIL_DECAY = 0.9


class _LightEmit(subject_fx._Emit):
    """1B's emitter with the light kinds."""

    def run(self, label):
        if self.st.get("mode") == "substitute":
            return self.sub_halation(label)
        return getattr(self, "k_" + self.it.get("kind"))(label)

    def sub_halation(self, label):
        """No matte: a soft bloom over the whole window (glow on the
        highlights), at a third of the light's intensity."""
        main, pw = self.window_pic(label)
        a, b, bl, out = (self.lab("ha"), self.lab("hb"), self.lab("hl"),
                         self.lab("ho"))
        inten = max(0.0, min(1.0, float(self.it.get("intensity") or 0.8)))
        self.p.append(f"[{pw}]split[{a}][{b}]")
        self.p.append(f"[{b}]gblur=sigma={max(2.0, 0.02 * self.H):.2f}[{bl}]")
        self.p.append(f"[{a}][{bl}]blend=all_mode=screen:"
                      f"all_opacity={0.33 * inten:.3f}[{out}]")
        return self._lay_window(main, out)

    def _lay_window(self, main, layer):
        w, out = self.lab("w"), self.lab("o")
        self.p.append(f"[{layer}]format=yuv420p"
                      f"{subject_fx.repad(self.f0, self.F)}[{w}]")
        expr = fxprims.enable_expr([(self.f0, self.f1)])
        ov = fxprims.composite_overlay(self.p, main)
        self.p.append(f"[{main}][{w}]{ov}:enable='{expr}'[{out}]")
        return out

    def k_rim(self, label):
        raw = self.raw_mask()
        tint = self.colour("colour", "white")
        inten = max(0.0, min(1.0, float(self.it.get("intensity") or 0.8)))
        ra, rb = self.lab("ra"), self.lab("rb")
        self.p.append(f"[{raw}]split[{ra}][{rb}]")
        inner = self.mask_window(ra, region=False)
        sig = max(0.8, 0.006 * self.H)
        outer, ring = self.lab("rd"), self.lab("rr")
        self.p.append(f"[{rb}]trim=start_frame={self.f0}:end_frame={self.f1},"
                      f"{self.restamp},format=gray,"
                      f"{self.firm_expr(subject_fx.FIRM_CRISP)},"
                      f"gblur=sigma={sig:.3f}[{outer}]")
        # the band just INSIDE the edge: the subject minus its eroded self,
        # softened — a rim, not an outline
        er = self.lab("re")
        self.p.append(f"[{inner}]erosion,erosion,erosion,"
                      f"gblur=sigma={sig:.3f}[{er}]")
        self.p.append(f"[{outer}][{er}]blend=all_mode=subtract,"
                      f"lut=c0='clip(val*{2.2 * inten:.4f},0,255)'[{ring}]")
        return self.screen_light(label, ring, tint)

    def k_trail(self, label):
        """The subject, tinted, through lagfun: where it WAS keeps glowing
        for a few frames (``decay``), screened over the picture."""
        raw = self.raw_mask()
        tint = self.colour("colour", "white")
        inten = max(0.0, min(1.0, float(self.it.get("intensity") or 0.8)))
        decay = float(self.it.get("decay") or TRAIL_DECAY)
        a = self.mask_window(raw, region=False, intensity=inten)
        lit, out = self.lab("tl"), self.lab("tt")
        blur = max(1.0, 0.004 * self.H)
        # lagfun on the light alpha: the trail is the max of the decayed
        # past (it works on any gray stream)
        self.p.append(f"[{a}]lagfun=decay={decay:.3f},"
                      f"gblur=sigma={blur:.2f}[{lit}]")
        self.p.append(f"[{lit}]lut=c0='clip(val*1.0,0,255)'[{out}]")
        return self.screen_light(label, out, tint)

    def k_dissolve(self, label):
        """Light climbs over the figure, then the figure is gone into the
        room: the subject is filled by a normalized-convolution blur of
        what surrounds it (sum of the picture outside the subject /
        sum of the outside, both blurred), so its silhouette dissolves into
        the room's own colours — never a smear of the figure itself."""
        it = self.it
        n = self.n
        dur_f = n
        if it.get("duration_s"):
            dur_f = max(2, min(n, int(round(float(it["duration_s"])
                                            * self.F))))
        peak = max(1, int(round(dur_f * DISSOLVE_PEAK)))
        tint = self.colour("colour", "white")
        inten = max(0.0, min(1.0, float(it.get("intensity") or 0.9)))
        raw = self.raw_mask()
        r1, r2 = self.lab("d1"), self.lab("d2")
        self.p.append(f"[{raw}]split[{r1}][{r2}]")
        main, pw = self.window_pic(label)
        L = self.lab
        pa, pb = L("pa"), L("pb")
        self.p.append(f"[{pw}]split[{pa}][{pb}]")
        # the subject, grown a little so its soft edge is not the room
        m = self.mask_window(r1, region=False, grow_px=6.0)
        mi, mf = L("mi"), L("mf")
        self.p.append(f"[{m}]split[{mi}][{mf}]")
        sig = max(6.0, 0.05 * self.H)
        inv, i1, i2, pf, num, den, room = (L("iv"), L("i1"), L("i2"),
                                           L("pf"), L("nu"), L("de"),
                                           L("ro"))
        self.p.append(f"[{mi}]negate,format=gbrpf32,split[{i1}][{i2}]")
        self.p.append(f"[{pb}]{subject_fx.TO_RGB},format=gbrpf32[{pf}]")
        self.p.append(f"[{pf}][{i1}]blend=all_mode=multiply,"
                      f"gblur=sigma={sig:.2f}[{num}]")
        self.p.append(f"[{i2}]gblur=sigma={sig:.2f}[{den}]")
        self.p.append(f"[{num}][{den}]blend=all_mode=divide,format=gbrp,"
                      f"{subject_fx.TO_YUV}[{room}]")
        del inv
        # the room's alpha over the figure: 0 until the peak, then up to
        # full at the end of the dissolve (frame-indexed: exact)
        ramp = L("fr")
        expr = (f"if(lt(N,{peak}),0,min(1,(N-{peak})/"
                f"{max(1, dur_f - peak)}))")
        self.p.append(f"[{mf}]geq=lum='lum(X,Y)*{expr}'[{ramp}]")
        fr, fa, gone = L("fa"), L("fb"), L("gn")
        self.p.append(f"[{room}]format=yuva420p[{fr}]")
        self.p.append(f"[{fr}][{ramp}]alphamerge[{fa}]")
        self.p.append(f"[{pa}][{fa}]overlay=format=auto,format=yuv420p"
                      f"[{gone}]")
        base = self._lay_window(main, gone)
        # the light: a glow rising to the peak, then dying with the figure
        light = self.glow_alpha(r2, 0.07, inten)
        lw = L("lw")
        env = (f"if(lt(N,{peak}),pow(N/{peak},1.6),max(0,1-(N-{peak})/"
               f"{max(1, int(dur_f * 0.3))}))")
        self.p.append(f"[{light}]geq=lum='clip(lum(X,Y)*1.6*({env}),0,255)'"
                      f"[{lw}]")
        return self.screen_light(base, lw, tint)


class _Light:
    FILTERS = FILTERS
    KEEPS_FRAME_TAGS = KEEPS_FRAME_TAGS

    def prepare(self, plan, item):
        if not plan.W or not plan.H or not plan.workdir:
            return ("omit", "no workdir/geometry for a light device")
        st = {"mode": "full", "kind": item.get("kind"),
              "space": "screen" if item.get("stage") == "screen"
              else "world"}
        subject_fx._state(plan)[item["id"]] = st
        subject_fx.plan_edl(plan)
        subject_fx.render_env(plan)
        info = fxgraph.request_matte(plan, item, "person", st["space"])
        status = (info or {}).get("status")
        if status not in ("ready", "partial"):
            st["mode"] = "substitute"
            return ("substitute", ((info or {}).get("reason")
                                   or "no person matte") +
                    " — a soft bloom stands in")
        return None

    def stage(self, ctx, items):
        label = ctx.label
        edl_cache = subject_fx.plan_edl(ctx.plan)
        for it in sorted(items, key=lambda i: (float(i["start"]), i["id"])):
            st = subject_fx._state(ctx.plan).get(it["id"]) or {"mode": "full"}
            mark = len(ctx.parts)
            try:
                label = _LightEmit(ctx, it, st, edl_cache).run(label)
            except Exception as e:  # noqa: BLE001 — this item only
                del ctx.parts[mark:]
                ctx.note(it, "omit", f"compile failed ({type(e).__name__}: "
                         f"{str(e)[:120]})")
        return label

    def cost_ms(self, plan, item):
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        per = {"trail": 9.0, "dissolve": 40.0, "rim": 14.0}.get(
            item.get("kind"), 20.0)
        return (f1 - f0) * per * mpx + 80.0


COMPILER = _Light()
