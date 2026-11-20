"""The one result shape every harness v2 tool returns (HARNESS_V2.md section 3).

``changed`` and ``measured`` are computed from the EDL by the tools; the model
never writes them. The envelope carries no instruction text and no taste
advisories: ``check`` lists looks that would verify a change, as pointers.
v1's ``tool_outcome`` is left alone.
"""
import json

DONE = "done"
DONE_WITH_REPAIRS = "done_with_repairs"
PARTIAL = "partial"
WORKING = "working"
FAILED = "failed"
STATUSES = (DONE, DONE_WITH_REPAIRS, PARTIAL, WORKING, FAILED)

# Text budgets for the model's copy; the full lists stay in to_dict().
_MAX_LINES = 40


class Envelope:
    def __init__(self, tool="", changed="", measured=None, repairs=None,
                 not_applied=None, running=None, findings=None, see=None,
                 check=None, undo_to=None, version=None, body="",
                 failed=False, applied=0, data=None, on_screen=None):
        self.tool = tool
        # on_screen: what the film shows and plays now, one line per layer
        self.on_screen = list(on_screen or [])
        self.changed = changed or ""
        self.measured = dict(measured or {})
        self.repairs = list(repairs or [])
        self.not_applied = list(not_applied or [])
        self.running = dict(running) if running else None
        self.findings = list(findings or [])
        # see: [(label, local image path)] handed to the caller's own eyes
        self.see = list(see or [])
        self.check = list(check or [])
        self.undo_to = undo_to
        self.version = version
        # body: a read's own answer (a transcript page, a shot list ...)
        self.body = body or ""
        self.failed = bool(failed)
        self.applied = int(applied or 0)
        self.data = data

    # -- status -----------------------------------------------------------
    @property
    def status(self):
        if self.running:
            return WORKING
        if self.failed:
            return FAILED
        if self.not_applied:
            # Something landed beside what could not: partial. Nothing at
            # all could be done: failed (the rows name what works instead).
            return PARTIAL if (self.applied or self.changed_anything()) \
                else FAILED
        if self.repairs:
            return DONE_WITH_REPAIRS
        return DONE

    def changed_anything(self):
        return bool(self.changed) and not self.changed.startswith("already so")

    # -- builders ---------------------------------------------------------
    def repair(self, line):
        if line and line not in self.repairs:
            self.repairs.append(str(line))
        return self

    def skip(self, op_index, why, instead=""):
        row = {"op": op_index, "why": str(why)[:600]}
        if instead:
            row["instead"] = str(instead)[:300]
        self.not_applied.append(row)
        return self

    def finding(self, line):
        if line:
            self.findings.append(str(line)[:600])
        return self

    def image(self, label, path):
        if path:
            self.see.append((str(label), str(path)))
        return self

    # -- output -----------------------------------------------------------
    def to_dict(self):
        return {"tool": self.tool, "status": self.status,
                "changed": self.changed, "measured": self.measured,
                "repairs": self.repairs, "not_applied": self.not_applied,
                "running": self.running, "findings": self.findings,
                "see": [label for label, _ in self.see],
                "check": self.check, "undo_to": self.undo_to,
                "version": self.version, "on_screen": self.on_screen}

    def text(self):
        """The compact copy the model reads (images travel separately)."""
        lines = [f"status: {self.status}"]
        if self.changed:
            lines.append(f"changed: {self.changed}")
        if self.measured:
            lines.append("measured: " + json.dumps(
                self.measured, ensure_ascii=False, default=str,
                separators=(", ", ": "))[:1500])
        if self.repairs:
            lines.append("repairs:")
            lines.extend(f"- {r}" for r in self.repairs[:_MAX_LINES])
            if len(self.repairs) > _MAX_LINES:
                lines.append(f"- ... {len(self.repairs) - _MAX_LINES} more")
        if self.not_applied:
            lines.append("not_applied:")
            for row in self.not_applied[:_MAX_LINES]:
                line = f"- op {row.get('op')}: {row.get('why')}"
                if row.get("instead"):
                    line += f" | instead: {row['instead']}"
                lines.append(line)
        if self.running:
            lines.append("running: " + json.dumps(self.running,
                                                  default=str))
        if self.findings:
            lines.append("findings:")
            lines.extend(f"- {f}" for f in self.findings[:_MAX_LINES])
        if self.on_screen:
            lines.append("on screen now (film seconds):")
            lines.extend(f"- {line}" for line in self.on_screen[:24])
        if self.body:
            lines.append(self.body)
        if self.see:
            lines.append("see: " + "; ".join(label for label, _ in self.see))
        if self.check:
            lines.append("check: " + "; ".join(self.check))
        if self.undo_to is not None:
            lines.append(f"undo_to: {self.undo_to}")
        return "\n".join(lines)


def read(tool, body, see=None, measured=None, coverage="", findings=None,
         running=None, data=None):
    """A read's envelope: its answer plus the coverage line."""
    text = body or ""
    if coverage:
        text = f"coverage: {coverage}\n" + text
    return Envelope(tool=tool, body=text, see=see, measured=measured,
                    findings=findings, running=running, data=data)


def failed(tool, why, instead=""):
    env = Envelope(tool=tool, failed=True)
    env.skip(0, why, instead)
    return env
