#!/bin/bash
# Add a render box in one command, from the owner's Mac (box/ADD_BOX.md):
#
#   box/host/add_box.sh <ipv4> [box id, default h2] [slot plan, default box-<id>.json]
#
# Run it from a hustler-server checkout of main. It
#   1. reaches the new server as root with ~/.ssh/valmera_box_ed25519,
#   2. hardens it (harden.sh: key-only SSH, ufw 22+443, fail2ban, Docker),
#   3. installs admission, provisioning, Caddy (TLS for <ip>.sslip.io) and
#      the update/clean timers with this box's slot plan (install.sh),
#   4. delivers the executor secrets through the box-provision workflow
#      (GitHub OIDC; a box-only push adds the box's URL; no value is ever
#      printed or copied by hand),
#   5. waits for the first build: every slot on main's worker fingerprint,
#   6. lists the box in box_routing as smoke-only and runs the parity smoke
#      (owner-account renders on both boxes, SSIM gate),
#   7. promotes it: the factory's home; box 1 customer-first plus factory
#      overflow.
# Every step is idempotent: re-running after a failure resumes.
set -euo pipefail

ip=${1:?usage: add_box.sh <ipv4> [box id] [slot plan]}
id=${2:-h2}
plan=${3:-box-$id.json}
key=${BOX_SSH_KEY:-$HOME/.ssh/valmera_box_ed25519}
env_file=${VALMERA_ENV_FILE:-/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env}
py=${VALMERA_PY:-/Users/masaoodi/Documents/Valmera/hustler-server/backend/.venv/bin/python}
repo=$(cd "$(dirname "$0")/../.." && pwd)
remote=git@github.com:ABO3SKRALMASAOODI/hustler-server.git
api=https://api.github.com/repos/ABO3SKRALMASAOODI/hustler-server
host="${ip//./-}.sslip.io"
url="https://$host"
workflow=.github/workflows/box-provision.yml
out_dir=${ADD_BOX_OUT:-$repo/box-$id-smoke}

[[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "not an IPv4 address: $ip" >&2; exit 2; }
[[ $id =~ ^h([2-9]|[1-9][0-9])$ ]] || { echo "box id must be h2..h99 (h1 is box 1)" >&2; exit 2; }
[[ $plan =~ ^box[a-z0-9-]*\.json$ ]] || { echo "bad slot plan name $plan" >&2; exit 2; }
test -f "$repo/box/host/$plan" || { echo "no slot plan box/host/$plan in this checkout" >&2; exit 2; }
test -r "$key" || { echo "no SSH key $key" >&2; exit 2; }
"$py" -c 'import psycopg2' || { echo "$py has no psycopg2" >&2; exit 2; }
mkdir -p "$out_dir"

ssh_() { ssh -i "$key" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=15 -o ServerAliveInterval=30 root@"$ip" "$@"; }
step() { printf '\n== %s  (%s)\n' "$*" "$(date -u +%H:%M:%SZ)"; }
gh_get() { curl -sS --max-time 30 -H "Accept: application/vnd.github+json" "$api$1"; }

step "1/7 reach $ip"
ssh_ 'set -e; . /etc/os-release; echo "$PRETTY_NAME, $(nproc) vCPU, $(free -g | awk "/Mem:/{print \$2}") GB RAM, $(df -BG --output=avail / | tail -1 | tr -d " ") free"'
ssh_ '. /etc/os-release; [ "$VERSION_ID" = "24.04" ]' || { echo "Ubuntu 24.04 expected" >&2; exit 1; }

step "2/7 harden"
ssh_ 'bash -s' < "$repo/box/host/harden.sh"

step "3/7 install ($host, plan $plan)"
ssh_ "set -e
  test -d /opt/valmera/repo/.git || git clone -q --filter=blob:none \
    https://github.com/ABO3SKRALMASAOODI/hustler-server.git /opt/valmera/repo
  git -C /opt/valmera/repo fetch -q origin main
  git -C /opt/valmera/repo checkout -q --detach --force FETCH_HEAD
  test -f /opt/valmera/repo/box/host/$plan || { echo 'main has no box/host/$plan: push it first' >&2; exit 1; }
  BOX_HOSTNAME=$host BOX_PLAN=$plan bash /opt/valmera/repo/box/host/install.sh"
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url/ping" || true)
  [ "$code" = 200 ] && break
  sleep 5
done
[ "$code" = 200 ] || { echo "$url/ping answered $code (Caddy TLS?)" >&2; exit 1; }
echo "$url answers over TLS"

step "4/7 secrets through the box-provision workflow (OIDC)"
if ! ssh_ 'awk -F= "length(\$2)>0{print \$1}" /etc/valmera/executor.env' | grep -qx DATABASE_URL; then
  wt=$(mktemp -d "${TMPDIR:-/tmp}/add-box-XXXX")
  git -C "$repo" fetch -q "$remote" main
  git -C "$repo" worktree add -q --detach "$wt" FETCH_HEAD
  if ! grep -q "BOX_URLS: .*$url" "$wt/$workflow"; then
    sed -i.bak -E "s#^(      BOX_URLS: .*)\$#\\1 $url#" "$wt/$workflow" && rm -f "$wt/$workflow.bak"
    grep -q "BOX_URLS: .*$url" "$wt/$workflow" || { echo "could not add $url to $workflow" >&2; exit 1; }
    git -C "$wt" add "$workflow"
    git -C "$wt" -c user.name=ABO3SKRALMASAOODI -c user.email=shmarymuslim@gmail.com \
      commit -q -m "Box $id: the provision workflow delivers executor secrets to $url" \
      -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
    GIT_SSH_COMMAND='ssh -o BatchMode=yes' git -C "$wt" push -q "$remote" HEAD:main
  fi
  sha=$(git -C "$wt" rev-parse HEAD)
  git -C "$repo" worktree remove --force "$wt"
  echo "waiting for box-provision on ${sha:0:8}"
  for i in $(seq 1 60); do
    run=$(gh_get "/actions/runs?head_sha=$sha&per_page=20" | "$py" -c '
import json, sys
runs = [r for r in json.load(sys.stdin).get("workflow_runs", [])
        if r.get("name") == "box-provision"]
print((runs[0]["status"] + " " + str(runs[0]["conclusion"])) if runs else "none")')
    case "$run" in "completed success") break;; "completed "*) echo "box-provision: $run" >&2; exit 1;; esac
    sleep 20
  done
fi
names=$(ssh_ 'awk -F= "length(\$2)>0{print \$1}" /etc/valmera/executor.env | sort | tr "\n" " "')
echo "secrets on the box (names only): $names"
for need in REMOTE_EXECUTOR_SECRET DATABASE_URL S3_ACCESS_KEY_ID; do
  [[ " $names " == *" $need "* ]] || { echo "missing $need on the box" >&2; exit 1; }
done

step "5/7 first build and slot roll (about 20-40 minutes)"
ssh_ 'systemctl start --no-block valmera-update.service'
want=$(ssh_ 'cd /opt/valmera/repo/worker && python3 -c "import version; print(version.code_version())"')
echo "worker fingerprint on main: $want"
for i in $(seq 1 120); do
  state=$(ssh_ 'curl -s -H "X-Admin-Token: $(cat /etc/valmera/admin.token)" localhost:8090/admin/state' || true)
  ready=$(printf '%s' "$state" | "$py" -c '
import json, sys
want, box_id = sys.argv[1], sys.argv[2]
try:
    s = json.load(sys.stdin)
except ValueError:
    print("no-state"); raise SystemExit
detail = s.get("slot_detail") or []
ok = [d for d in detail if d.get("ready") and d.get("release") == want]
print("ready" if detail and len(ok) == len(detail) and s.get("box_id") == box_id
      else f"{len(ok)}/{len(detail)} slots on {want}, box_id={s.get(\"box_id\")}")' "$want" "$id")
  [ "$ready" = ready ] && break
  [ $((i % 6)) = 0 ] && echo "  $ready"
  sleep 30
done
[ "$ready" = ready ] || { echo "the box did not converge: $ready" >&2; exit 1; }
echo "every slot serves $want; the box answers as $id"

step "6/7 parity smoke (owner account only)"
"$py" "$repo/box/add_box.py" --env-file "$env_file" register --id "$id" --url "$url" >/dev/null
sleep 30   # the dispatcher re-reads box_routing every 15 s
"$py" "$repo/box/add_box.py" --env-file "$env_file" smoke --id "$id" --ip "$ip" \
  --key "$key" --out "$out_dir/smoke.json"

step "7/7 promote: $id is the factory's home; h1 customers first plus factory overflow"
"$py" "$repo/box/add_box.py" --env-file "$env_file" promote --id "$id" \
  --report "$out_dir/smoke.json"
echo "done: $id at $url. Smoke report: $out_dir/smoke.json"
echo "rollback (no new work on $id; its calls stay reachable):"
echo "  $py $repo/box/add_box.py demote --id $id"
