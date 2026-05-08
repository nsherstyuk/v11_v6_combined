# Mac migration — status & next steps

**Last updated:** 2026-05-05 by Cowork  
**Source runbook:** `docs/ops/macbook_migration.md`

---

## Where we are

| Phase | Topic | Status |
|---|---|---|
| 1 | Mac prerequisites (Xcode CLT, Homebrew, Python 3.11, IB Gateway) | ✅ Done — Python 3.11.15 installed via Homebrew at `/opt/homebrew/opt/python@3.11/`, IBKR Gateway installed |
| 2 | Codebase + venv | ✅ Done — repo cloned to `~/code/ibkr_grok_wing_agent/`, `.venv` created, all imports + 550 tests pass (2026-05-08: added `ib_insync` and `pytest-asyncio` to requirements.txt — production code imports `ib_insync` directly, async tests need the plugin; Cowork's original list missed both) |
| 3 | Secrets (`.env`) | ✅ Done 2026-05-08 — `.env` in repo root, `chmod 600`, 5 keys present (IB_HOST/IB_PORT/IB_CLIENT_ID + 2 secret-named), no Windows paths |
| 4 | IBC + IB Gateway supervision | ✅ Done 2026-05-08 — IBC 3.23.0 at `~/ibc/`, `config.ini` from template w/ creds (0600), `gatewaystartmacos.sh` patched (port guard + `TWS_MAJOR_VRSN=10.46` + `IBC_PATH=~/ibc`), backup at `*.bak` |
| 5 | Claude Code conversation history | ❌ Not started (optional) |
| 6 | First Mac run of v11 paper | 🟡 In progress 2026-05-08 — Gateway up, port 4002 listening (`*:4002`, consider tightening to localhost). v11 paper run pending. |
| 7 | Decommission Windows host | ❌ Blocked on 6 |

## What I prepared (in this repo, ready to use)

- `docs/ops/ibc_config.mac.template.ini` — Mac-friendly IBC config skeleton with placeholders for credentials and the safe restart times (22:00 ET / 14:00 ET ColdRestart) that fixed the 2026-05-01 incident.
- `docs/ops/gatewaystart_port_guard.snippet.sh` — port-listening guard snippet to insert into IBC's shipped `gatewaystartmacos.sh`. Mirrors the Windows StartGateway.bat fix.

Neither file contains secrets. Both are safe to commit.

---

## What Nick has to do (in order)

### Step 1 — Verify Phase 2 venv actually works

Open Terminal on the Mac:

```bash
cd ~/code/ibkr_grok_wing_agent
source .venv/bin/activate
python -c "import ib_async, openai, pandas, pydantic, dotenv; print('imports OK')"
python -m pytest tests/ v11/tests/ -q
```

Expected: `imports OK`, then a pytest run (some integration tests may skip without IBKR/network — those skips are fine; failures are not).

If any import fails, run `pip install -r requirements.txt` and try again.

### Step 2 — Phase 3 — Transfer `.env`

Move `C:\ibkr_grok-_wing_agent\.env` from the Windows machine to the Mac via **encrypted USB** or **`scp` over your local network**. Do not use email, Slack, Dropbox, or any cloud sync. Drop into the repo root:

```bash
# after the file is at ~/code/ibkr_grok_wing_agent/.env
chmod 600 ~/code/ibkr_grok_wing_agent/.env
grep -E "^[A-Z_]+=" ~/code/ibkr_grok_wing_agent/.env | grep -v -E "(KEY|TOKEN|PASS|SECRET)" | head
```

The last line previews non-secret keys so you can confirm the file landed without me ever seeing the secrets.

If the Windows `.env` had any `C:\...` paths, edit them to Mac equivalents.

### Step 3 — Phase 4 — Install IBC for macOS (skip if already installed)

Check first:

```bash
ls ~/ibc/ 2>/dev/null && echo "IBC already installed" || echo "IBC not installed"
```

If not installed:

1. Download the latest `IBCMacos-<ver>.zip` from https://github.com/IbcAlpha/IBC/releases
2. Unzip into `~/ibc/`:
   ```bash
   mkdir -p ~/ibc
   unzip -o ~/Downloads/IBCMacos-*.zip -d ~/ibc/
   chmod +x ~/ibc/gatewaystartmacos.sh ~/ibc/twsstartmacos.sh 2>/dev/null
   ```

### Step 4 — Phase 4 — Create `~/ibc/config.ini` from the template

```bash
mkdir -p ~/ibc
cp ~/code/ibkr_grok_wing_agent/docs/ops/ibc_config.mac.template.ini ~/ibc/config.ini
chmod 600 ~/ibc/config.ini
$EDITOR ~/ibc/config.ini   # or: nano / vim / open -e
```

Fill in the two empty fields:
```
IbLoginId=<your IBKR paper username>
IbPassword=<your IBKR paper password>
```

Leave everything else as-is. Save.

### Step 5 — Phase 4 — Add port guard to `~/ibc/gatewaystartmacos.sh`

```bash
cat ~/code/ibkr_grok_wing_agent/docs/ops/gatewaystart_port_guard.snippet.sh
```

Open `~/ibc/gatewaystartmacos.sh` in any editor, find the shebang line (e.g. `#!/bin/bash`), paste the guard block (lines starting with `if lsof ...`) immediately after it. Save.

### Step 6 — Phase 6 — First IBC + Gateway launch

```bash
~/ibc/gatewaystartmacos.sh
```

What to expect on first launch:
- IBC drives the Gateway login dialog using the credentials in `~/ibc/config.ini`
- IBKR will likely send a 2FA prompt to your IBKR mobile app — approve it
- After login, the Gateway window minimizes/idles; IBC keeps it alive
- Confirm port 4002 is listening (in a second Terminal):
  ```bash
  lsof -nP -iTCP:4002 -sTCP:LISTEN
  ```
  Expect a single line showing a `java` process bound to `127.0.0.1:4002`.

If it fails: see "Common gotchas" in `docs/ops/macbook_migration.md`.

### Step 7 — Phase 6 — First v11 paper run

```bash
cd ~/code/ibkr_grok_wing_agent
source .venv/bin/activate
python -m v11.live.run_live --live --no-llm
```

This is the equivalent of the Windows `start_v11.bat --live --no-llm`. Strategy is XAUUSD ORB only; LLM bypassed.

The status line should show clear Mac paths and connect successfully. The first **proof of life** is a real bracket order placed during the trade window (08:00–16:00 UTC = 04:00–12:00 ET). Per the May 1 postmortem, that's the unfinished business of the entire migration.

### Step 8 — Phase 5 (optional) — Claude Code conversation history

Only do this if you want to preserve prior Claude Code sessions for v11. Skip if you're moving to Cowork only.

Procedure is in `docs/ops/macbook_migration.md` Phase 5.

---

## Things I CANNOT do for you (why)

| Task | Why not |
|---|---|
| Run `pip install`, `pytest`, `gatewaystartmacos.sh` on your Mac | My shell is a Linux sandbox, not your Mac terminal |
| Touch your `.env` | Contains the xAI API key — sensitive material, must be moved by you |
| Touch `~/ibc/config.ini` | Contains your IBKR password in plaintext — must be filled in by you |
| Approve the IBKR 2FA prompt | Requires your IBKR mobile app |
| Decide when paper has "proven itself" | Judgement call — at minimum, one real fill in the trade window |

---

## When you're stuck

If anything in steps 1–7 fails or behaves unexpectedly, paste the error or symptom into chat and I will diagnose. Do not paste credentials.
