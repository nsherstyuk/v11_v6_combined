# Macbook migration runbook

How to move the v11 codebase, secrets, IBKR/IBC supervision, and Claude
Code conversation history from the Windows host to a Macbook.

**Source host (current):** Windows 11, repo at `C:\ibkr_grok-_wing_agent\`.
**Target host:** macOS, repo will live at `~/code/ibkr_grok_wing_agent/` (suggested).

## Phase 0 — pre-flight (already done on Windows)

These were completed before generating this doc:

- All commits pushed to `origin/master` on `github.com/nsherstyuk/v11_v6_combined`.
- Untracked research scripts and code-review docs committed.
- `.gitignore` extended for results cache, NYMEX CSVs, PDFs, one-off LLM transcripts.

## Phase 1 — Mac prerequisites

Install in this order:

1. **Xcode Command Line Tools:** `xcode-select --install` (gives you git, make, cc).
2. **Homebrew:** https://brew.sh
3. **Python 3.11+:** `brew install python@3.11`
4. **IB Gateway for macOS:** download stable from
   https://www.interactivebrokers.com/en/trading/ibgateway-stable.php —
   installs to `/Applications/IB Gateway <version>/`.
5. **IBC for macOS:** download from https://github.com/IbcAlpha/IBC/releases
   (the `IBCMacos-<ver>.zip` package). Standard install path is `~/ibc/`.
6. **(Optional) iTerm2 / your shell of choice.** `bash` works fine; `zsh` is the macOS default.

## Phase 2 — codebase

```bash
mkdir -p ~/code && cd ~/code
git clone https://github.com/nsherstyuk/v11_v6_combined.git ibkr_grok_wing_agent
cd ibkr_grok_wing_agent
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Sanity check:

```bash
python -c "import ib_async, openai, pandas, pydantic, dotenv; print('imports OK')"
python -m pytest tests/ -q   # if tests exist, should pass
```

## Phase 3 — secrets (`.env`)

The `.env` file is **not in git**. Transfer it manually:

1. On Windows, `C:\ibkr_grok-_wing_agent\.env` (~239 bytes).
2. Move via **encrypted USB stick** or **`scp` over your local network**, not email/Slack/cloud sync.
3. Drop into the Mac repo root: `~/code/ibkr_grok_wing_agent/.env`.
4. `chmod 600 .env`.

If the `.env` references absolute Windows paths, edit them to the Mac
equivalents. Look for things like `C:\...` or `/c/...`.

## Phase 4 — IBC + IB Gateway on macOS

On macOS, IBC uses **`~/ibc/config.ini`** (not `~/Documents/IBC/`).

### Active config

Create `~/ibc/config.ini` from scratch — **do not copy the Windows
config**. It has Windows line endings (CRLF) and Windows-style paths
that will silently break IBC on macOS.

Reference the Windows snapshot at `docs/ops/ibc_config.ini.snapshot` for
non-secret values, then add credentials manually.

Key values that must match the Windows host:

```ini
TradingMode=paper
IbLoginId=<your IB user>
IbPassword=<your IB password>
AutoRestartTime=22:00
ColdRestartTime=14:00
TwoFactorTimeoutAction=exit
ReadOnlyApi=no
OverrideTwsApiPort=4002
```

`AutoRestartTime=22:00` (ET) is critical — picking the IBKR-default
04:00 ET collides with the ORB trade-window open and was the root cause
of the 2026-05-01 zero-trades-for-2-weeks incident.

### Launcher

IBC ships a `gatewaystartmacos.sh` script. Edit it the same way the Windows
`StartGateway.bat` was edited: add a port-listening guard so a manual
re-launch while Gateway is already up does not produce a double-login
lockout.

```bash
# at top of gatewaystartmacos.sh, after shebang:
if lsof -nP -iTCP:4001 -sTCP:LISTEN > /dev/null 2>&1; then
    echo "ABORT: port 4001 (live API) already listening — Gateway already up"
    exit 1
fi
if lsof -nP -iTCP:4002 -sTCP:LISTEN > /dev/null 2>&1; then
    echo "ABORT: port 4002 (paper API) already listening — Gateway already up"
    exit 1
fi
```

### Auto-start at login (optional)

Use `launchd`, not cron. Create
`~/Library/LaunchAgents/com.ibc.gateway.plist` with `RunAtLoad=true`
pointing at `~/ibc/gatewaystartmacos.sh`. Standard recipe — defer to IBC's
own macOS docs in their wiki.

## Phase 5 — Claude Code conversation history + memory

Claude Code stores per-project state under
`~/.claude/projects/<encoded-abs-path>/`. The folder name is derived
from the absolute path of the repo, so it differs between Windows and
Mac.

| Host | Folder name |
|---|---|
| Windows (current) | `C--ibkr-grok--wing-agent` |
| Mac (suggested path `~/code/ibkr_grok_wing_agent`) | `-Users-<you>-code-ibkr_grok_wing_agent` |

To preserve memory + transcripts:

```bash
# on Windows, tar up the project dir (37M total)
cd "$USERPROFILE/.claude/projects"
tar czf ~/Desktop/claude_project_v11.tgz "C--ibkr-grok--wing-agent"

# transfer .tgz to Mac via USB / scp / AirDrop

# on Mac:
mkdir -p ~/.claude/projects
cd ~/.claude/projects
tar xzf ~/Desktop/claude_project_v11.tgz
# rename folder to match the Mac repo path encoding:
mv "C--ibkr-grok--wing-agent" "-Users-$(whoami)-code-ibkr_grok_wing_agent"
```

Verify by launching Claude Code from `~/code/ibkr_grok_wing_agent` and
running `/memory` — your saved memories should be present.

If you cannot get Claude Code to pick up the renamed folder, the
session `.jsonl` files are still readable directly with any text tool;
they're not lost.

## Phase 6 — first run on Mac

```bash
cd ~/code/ibkr_grok_wing_agent
source .venv/bin/activate

# 1. start IB Gateway via IBC, log in (paper mode), confirm port 4002 listening:
~/ibc/gatewaystartmacos.sh
lsof -nP -iTCP:4002 -sTCP:LISTEN   # should show one entry

# 2. run v11 live in paper mode (LLM disabled):
python -m v11.live.run_live --live --no-llm
```

Equivalent of `start_v11.bat --live --no-llm`. The repo does not yet
have a Mac wrapper script — make one if you want symmetry with the
Windows launcher.

## Phase 7 — decommission Windows host

Once a full ORB trading day on the Mac has produced expected behavior
(status line shows the new honest skip-reason; or a real bracket is
placed and a fill recorded):

1. **Stop IB Gateway on Windows** via `C:\IBC\Stop.bat`. Verify port
   4001/4002 is no longer listening.
2. **Sign out of IBKR account** on Windows (so the next live login on
   Mac is clean).
3. **Wait 24 hours** before deleting anything from Windows — gives you
   one rollback path if Mac setup misbehaves.
4. After 24h: optional repo cleanup (the working tree contains
   gitignored runtime state — `v11/live/logs/`, `v11/live/state/`,
   `v11/live/trades/`. Safe to delete, but consider archiving
   `logs/` first if you want post-mortem material.)

## Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `pip install` fails on `ib_async` | Old pip | `pip install -U pip setuptools wheel` first |
| Gateway connects then immediately disconnects | Wrong password in `~/ibc/config.ini` | Check `IbPassword=` |
| `ImportError: No module named openai` after install | Wrong venv active | `which python` should be inside `.venv/bin/` |
| Claude Code shows no memory | Folder name mismatch under `~/.claude/projects/` | Rename to match the Mac absolute path encoding (see phase 5 table) |
| Status line says `LLM rejected today` even with `--no-llm` | You're running an old commit pre-`8d3db75` | `git pull origin master` |
| 2FA loop on first login from Mac | New device, IBKR may require fresh 2FA enrollment | Approve via IBKR mobile app; IBC will auto-resume |

## See also

- `docs/ops/ibc_setup.md` — Windows-side IBC operational doc (analogue of phase 4 above)
- `docs/journal/2026-05-01_paper_zero_trades_root_cause_and_ibc_fixes.md` —
  why `AutoRestartTime=22:00` matters
