#!/bin/zsh
# The default narrator on this Mac, from nothing: the Phonon runtime (ptts, no PyTorch) in the narrate venv and the
# Phonon 31M weights plus the owner's voice (Easton) in NARRATE_MODELS/phonon, every file checked against phonon.json
# (Hub revisions pinned, sha256 per file). Safe to run again: a file already right is kept.
#
#   zsh setup_phonon.sh            # install and fetch
#   zsh setup_phonon.sh --check    # only say whether everything is in place
set -eu
HERE=${0:A:h}
VV=/Users/masaoodi/Documents/Valmera/.valmera
PY=${NARRATE_PY:-$VV/venv-narrate/bin/python}
DEST=${NARRATE_MODELS:-$VV/models/narrate}/phonon
PIN=$HERE/phonon.json

if [[ ! -x $PY ]]; then
  echo "no narrate venv at $PY: make it first (README.md, Setup), then run this again" >&2
  exit 1
fi

$PY - "$PIN" "$DEST" "${1:-}" <<'EOF'
import hashlib, json, os, subprocess, sys, urllib.request
pin, dest, mode = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
check = mode == "--check"
bad = []

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def hub(spec, name):
    repo, rev = spec[len("hf://"):].split("@")
    return f"https://huggingface.co/{repo}/resolve/{rev}/{name}"

# 1. the runtime
want = pin["runtime"].split("==")[1]
try:
    import ptts  # noqa: F401
    from importlib.metadata import version
    have = version("ptts")
except Exception:
    have = None
if have != want:
    if check:
        bad.append(f"ptts {have} (want {want})")
    else:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", pin["runtime"]], check=True)
        print("installed", pin["runtime"])

# 2. the weights and the voice
todo = [(n, f, hub(pin["model"], n)) for n, f in pin["files"].items()]
for name, v in pin["voice_files"].items():
    todo.append((v["file"], v, hub(pin["voice_catalog"], v["file"])))
for rel, meta, url in todo:
    p = os.path.join(dest, rel)
    if os.path.exists(p) and os.path.getsize(p) == meta["size"] and sha(p) == meta["sha256"]:
        continue
    if check:
        bad.append(rel)
        continue
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".part"
    urllib.request.urlretrieve(url, tmp)
    if sha(tmp) != meta["sha256"]:
        os.remove(tmp)
        sys.exit(f"{rel}: the download does not match phonon.json")
    os.replace(tmp, p)
    print("fetched", rel, meta["size"])
cat = os.path.join(dest, "voices.jsonl")
lines = "".join(v["line"] + "\n" for v in pin["voice_files"].values())
if not os.path.exists(cat) or open(cat).read() != lines:
    if check:
        bad.append("voices.jsonl")
    else:
        open(cat, "w").write(lines)

if bad:
    sys.exit("missing or wrong: " + ", ".join(bad))

# 3. one line, offline, from the folder
os.environ["HF_HUB_OFFLINE"] = "1"
import ptts
s = pin["settings"]
tts = ptts.TTS(dest, lang=s["lang"], quant=s["quant"], voice_catalog=cat)
x = tts.synth("The narrator is ready.", voice=pin["voices"][0], seed=7)
print(json.dumps({"ok": True, "model": dest, "voice": pin["voices"][0], "device": tts.device, "quant": tts.quant,
                  "seconds_of_speech": round(len(x) / tts.sample_rate, 2)}))
EOF
