# Setup: keys and secrets (T04)

You need two keys in a `.env` file in the project root for **live runs** and **building topic lists**. Run `cp .env.example .env` and fill them in. `.env` is git-ignored. **Never commit real keys**, and never paste them into chats, docs, issues or `.env.example`. Without keys, Replay, the showcase run and all offline tests still work.

| Key | What it's for | Cost | Who has it |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Calls the AI models (luna) for live runs and the topic classifier | Paid from prepaid credits | Get a key from the account owner by direct message or a password manager. Each key should have its own credit limit |
| `GITHUB_TOKEN` | Reads **other people's public repos** (the ones Sleeper analyzes): stars, forks, commits, releases, activity, READMEs. It isn't used for our own repo; pushing and pulling use your normal git login | Free | Everyone makes their own (below); rate limits are per token |

## Make your GitHub token (2 minutes)

Use a **classic** token with **no scopes**. It can only read public data, and it's the type we've tested. Fine-grained tokens returned `403` on some endpoints.

1. Go to github.com → Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token (classic).
2. Name it `sleeper-hackathon` and set it to expire in 7 days.
3. **Leave every scope box unticked.**
4. Generate the token (it starts with `ghp_`) and paste it into `.env` as `GITHUB_TOKEN=`.
5. Check it: `uv run pytest -m live` should pass.

## Spending safety

- The OpenRouter key has a **$50 limit**, set on the key itself, so calls fail once it's used up.
- Prepaid credits are a second cap. The account can never spend more than its balance.
- Use **luna** (the cheap model) for all development. Use terra only for demo runs.
- Check spending at any time: openrouter.ai → Activity.

## If a key leaks

Delete it straight away in OpenRouter (Keys) or GitHub (Developer settings), make a new one, and tell the team.

## Known machine issue

python.org's Python on macOS ships without SSL certificates (`CERTIFICATE_VERIFY_FAILED`). Use the project's `uv` environment, or run `/Applications/Python 3.x/Install Certificates.command` once.

## History note (2026-10-24)

During the hackathon a shared OpenRouter key and a GitHub token were briefly committed to `.env.example`. Both were **revoked** the same day, so the strings still visible in old commits are dead. Don't repeat it: keys live only in your local `.env`.
