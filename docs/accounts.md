# Claude multi-account setup

Goal: keep coding when one plan hits its limit by rotating accounts at a **user-defined usage %**.

## 1. Init config

```bash
effi accounts init
# creates ~/.config/effi/accounts.json  (mode 600)
```

Edit that file (or start from `config/accounts.example.json` in the repo).

## 2. API key accounts (recommended)

```json
{
  "switch_threshold_percent": 80,
  "accounts": [
    {
      "id": "work-primary",
      "label": "Work primary",
      "provider": "claude",
      "type": "api_key",
      "api_key_env": "ANTHROPIC_API_KEY_WORK",
      "usage_percent": 0,
      "priority": 1,
      "enabled": true
    },
    {
      "id": "work-secondary",
      "label": "Work secondary",
      "provider": "claude",
      "type": "api_key",
      "api_key_env": "ANTHROPIC_API_KEY_WORK2",
      "usage_percent": 0,
      "priority": 2,
      "enabled": true
    }
  ]
}
```

Put keys in the shell (not in the JSON file):

```bash
# ~/.zshrc
export ANTHROPIC_API_KEY_WORK="sk-ant-…"
export ANTHROPIC_API_KEY_WORK2="sk-ant-…"
```

```bash
effi accounts meter work-primary 0
effi accounts threshold 80    # switch when usage >= 80%
effi accounts select
effi doctor                   # "credentials resolvable" should be > 0
```

`effi` / `effi cloud` calls `accounts select` and exports `ANTHROPIC_API_KEY` for the active account.

## 3. Subscription / OAuth profiles (optional)

Claude Code OAuth lives in a config dir. Isolate one profile per account:

```json
{
  "id": "personal-sub",
  "type": "oauth_profile",
  "config_dir": "~/.config/effi/profiles/personal",
  "usage_percent": 0,
  "priority": 3,
  "enabled": true
}
```

1. Log in once with that profile (copy or create the Claude config under `config_dir`).
2. When selected, effi sets `CLAUDE_CONFIG_DIR` to that path.

Do **not** put subscription OAuth tokens into third-party routers/proxies (Anthropic ToS).

## 4. Metering

Anthropic does not always expose a simple “% used” for every plan in CLI form. Treat `usage_percent` as **you** update it:

```bash
# After checking claude.ai / console usage:
effi accounts meter work-primary 72
# If that crosses threshold, select rotates automatically
effi accounts list
```

Tips:
- Update meter at start of day and when you see rate-limit messages.
- Threshold is yours: `effi accounts threshold 70` for earlier rotation.

## 5. Daily flow

```bash
effi accounts list      # who is active / over threshold
effi doctor             # keys resolvable?
effi                    # cloud session with selected account
# hit limit → meter to 100 → next session uses next account
effi accounts meter work-primary 100
effi                    # should pick secondary if under threshold
```

## 6. Security

- Never commit `~/.config/effi/accounts.json` with real keys.
- Prefer `api_key_env` over inline `api_key`.
- `chmod 600` on config files (effi sets this on write).
