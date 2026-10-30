# Adding render box 2

The dispatcher already knows how to spread work across two boxes
(`worker/box_routing.py`, `boxes`). Until box 2 exists that code is dormant:
without a `boxes` list in app_kv `box_routing` there is one box, `h1`, and
every request, call id, wait and ledger write is exactly the single-box path
(`worker/tests/test_box_two_boxes.py::test_one_box_is_exactly_the_single_box_path`
compares it with a trace recorded before two-box admission existed).

## What to buy (owner)

- **Contabo Cloud VPS 16**: 16 vCPU (AMD EPYC), 64 GB RAM, 250 GB NVMe,
  **Ubuntu 24.04**, region **US West (Seattle)**, the same region as box 1.
- Price: about EUR 29.60 a month on a 24-month term, about EUR 37 a month on
  a monthly term, plus any US-West location fee shown at checkout (roughly
  US$35-45 a month). A fixed cost: it does not grow with users.
- At checkout, under SSH keys, paste the contents of
  `~/.ssh/valmera_box_ed25519.pub` for root (no password login is needed).
- Then send the new server's IPv4 address. Nothing else is needed: the
  executor secrets reach the box through GitHub's OIDC workflow, never by
  hand.

## The one command

From a `hustler-server` checkout of `main` on the Mac (or any session's
worktree):

```bash
box/host/add_box.sh <ipv4>          # box id h2, slot plan box/host/box-h2.json
```

It is idempotent; re-run it after any failure and it resumes. In order it:

1. reaches the server as root with `~/.ssh/valmera_box_ed25519` and checks
   Ubuntu 24.04;
2. hardens it with `box/host/harden.sh` (key-only SSH, ufw 22 + 443,
   fail2ban, unattended security upgrades, swap, bounded journald, Docker
   with live-restore);
3. clones the public repository (blobless) and runs `box/host/install.sh`
   with `BOX_HOSTNAME=<ip-with-dashes>.sslip.io BOX_PLAN=box-h2.json`:
   admission, provisioning, Caddy (Let's Encrypt over TLS-ALPN, port 80
   stays closed), the 2-minute update timer and the 15-minute clean timer;
   `/etc/valmera/plan` makes `boxctl` keep `box-h2.json` current from main;
4. adds `https://<ip-with-dashes>.sslip.io` to `BOX_URLS` in
   `.github/workflows/box-provision.yml` with a box-only push (no executor
   release), waits for that workflow, and checks the secret NAMES on the box
   (values are never printed);
5. starts the first update and waits until every slot serves main's worker
   fingerprint and admission answers as `h2` (the first build takes about
   20-40 minutes; later updates take seconds);
6. lists the box in `box_routing` as smoke-only (no classes: only a pinned
   owner-account job can reach it) and runs the parity smoke
   (`box/add_box.py smoke`): about 10 of the owner's own recent EDLs
   rendered on box 1 and box 2 in the same minutes (two of them on the batch
   slots), plus three read-only MCP replays; the gate is every job done,
   every box-2 job run under a `bx-h2-` identity, equal durations, and each
   pair byte-identical or SSIM >= 0.999 (measured inside a box-2 slot);
7. promotes it: `h2` is the owner factory's home, `h1` serves customers
   first and takes factory overflow (with a 30-second overflow wait).

The smoke report is written to `box-h2-smoke/smoke.json` in the checkout.

**YouTube (owner step, before step 7):** the box reaches YouTube only
through the owner's IPRoyal ISP proxy (`YTDLP_PROXY`), which admits
whitelisted IPs only: add the new box's IPv4 under IPRoyal -> ISP Dedicated
-> Whitelisted IPs. Box 2's daily canary (`box/yt_canary.py`, app_kv
`youtube_canary:h2`) fails until it is whitelisted.

## How work spreads (the design)

```json
{"enabled": true, "stages": {...}, "owner_user_ids": [60], "skew": "hold",
 "boxes": [{"id": "h1", "url": "https://13-140-241-231.sslip.io",
            "classes": ["customer", "trial"],
            "accepts": ["customer", "trial", "owner"]},
           {"id": "h2", "url": "https://<box 2>.sslip.io",
            "classes": ["owner"],
            "accepts": ["customer", "trial", "owner"]}],
 "overflow_wait_s": 30}
```

- `classes`: the admission classes a box is home for; `accepts`: the classes
  it also takes as overflow.
- Each job asks its home box first, with admission's full wait (150 s).
- If that box refuses with proof that nothing ran (a capacity refusal, a
  drain or release switch, an unreachable box, a fenced lost launch), the
  same claim asks the next box at once under that box's own identity
  (`bx-h2-...`; `h1` keeps today's `bx-...` ids). This is Cloudflare's shard
  hop: the refused identity is terminal and its ledger row is closed before
  the new identity is recorded, the new identity was never accepted
  anywhere, the lease is re-checked first, and the executor's ownership
  fence runs only the call the ledger names.
- An ambiguous launch (a lost response) never moves: it reconnects to its
  own box.
- After every box of a round refused, the single-box backoff applies; after
  the in-claim window (30 min) the job returns to the Postgres queue unspent.
  Nothing ever falls back to Cloudflare.
- Each box keeps its own priority queue (customers, trial, owner factory)
  and its own owner caps: box 2 is sized as the factory's home (interactive
  10 of 12, batch 5 of 6, mcp 14 of 16), box 1 keeps today's caps so
  customers always find a slot.
- Children never move: inside a slot they go to that box's own admission.
- Every request about a call carries `X-Valmera-Box: <id>`, and each
  admission refuses a call meant for another box, so a routing edit can never
  make a reconnect mistake "missing here" for "lost".

## Rollback and removal

- `add_box.py demote --id h2`: no new work on h2 (box 1 is home of every
  class again); calls already on h2 stay reachable and finish there.
- Remove the entry only once h2 is idle. Never give a listed id another host.
- To take box 2 out of the provision workflow, remove its URL from
  `BOX_URLS`.

## By hand, if the script cannot run

```bash
ssh -i ~/.ssh/valmera_box_ed25519 root@<ip> 'bash -s' < box/host/harden.sh
ssh -i ~/.ssh/valmera_box_ed25519 root@<ip> 'git clone -q --filter=blob:none https://github.com/ABO3SKRALMASAOODI/hustler-server.git /opt/valmera/repo && BOX_HOSTNAME=<ip-dashed>.sslip.io BOX_PLAN=box-h2.json bash /opt/valmera/repo/box/host/install.sh'
# add the URL to BOX_URLS in .github/workflows/box-provision.yml; push (box-only)
python box/add_box.py register --id h2 --url https://<ip-dashed>.sslip.io
python box/add_box.py smoke --id h2 --ip <ip> --out smoke.json
python box/add_box.py promote --id h2 --report smoke.json
```
