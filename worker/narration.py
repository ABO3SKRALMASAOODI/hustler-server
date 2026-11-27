"""Render a scripted Studio narration with an installed offline voice."""
import os
import shutil
import subprocess


def _voices(command):
    rows = subprocess.check_output(command, text=True, timeout=15).splitlines()
    voices = []
    if command[0] == "say":
        for row in rows:
            fields = row.partition("#")[0].rsplit(None, 1)
            if len(fields) == 2:
                voices.append((fields[0].strip(), fields[1].replace("_", "-").lower()))
    else:
        for row in rows[1:]:
            fields = row.split()
            if len(fields) >= 5 and fields[0].isdigit():
                voices.append((fields[1], fields[1].lower()))
    return voices


def synthesize(script, locale, voice, directory):
    locale = (locale or "en-US").strip().lower().replace("_", "-")
    path = os.path.join(directory, "narration.wav")
    for engine, args in (("say", ["-v", "?"]), ("espeak-ng", ["--voices"])):
        if not shutil.which(engine):
            continue
        voices = _voices([engine, *args])
        requested = [v for v in voices if voice and v[0].casefold() == voice.casefold()]
        matching = [v for v in voices if v[1] == locale]
        related = [v for v in voices if v[1].split("-")[0] == locale.split("-")[0]]
        # Prefer standard narration voices over macOS novelty voices.
        preferred = ("Daniel", "Samantha", "Tessa")
        candidates = requested + sorted(matching, key=lambda v: v[0] not in preferred) + sorted(
            related, key=lambda v: v[0] not in preferred)
        if not candidates:
            continue
        name, language = candidates[0]
        command = (["say", "-v", name, "-o", path, "--file-format=WAVE",
                    "--data-format=LEI16@24000", "-f", "-"] if engine == "say" else
                   [engine, "-v", name, "-w", path, "--stdin"])
        subprocess.run(command, input=script, text=True, check=True, capture_output=True,
                       timeout=max(60, len(script) // 10))
        return path, name, language
    raise ValueError(f"No installed narration voice for {locale}")
