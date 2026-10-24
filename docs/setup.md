# Setup: keys and secrets (T04)

You need two keys in a `.env` file in the project root. Copy `.env.example` to `.env`, then fill it in. `.env` is git-ignored. Never commit it, and never paste keys into group chats, docs or issues.

| Key | What it's for | Cost | Who has it |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Calls the AI models: luna for development, terra for the demo | Paid from prepaid credits | **One shared team key.** Ask Roee for it by direct message or password manager |
| `GITHUB_TOKEN` | Reads **other people's public repos** (the ones Sleeper analyzes): stars, stargazers, commits, READMEs. It isn't used for our own repo; pushing and pulling use your normal git login | Free | Anyone whose code calls GitHub: the data track and the agent track. Everyone uses their own, because rate limits are per token |

## Make your GitHub token (2 minutes)

Use a **classic** token with **no scopes**. Fine-grained tokens can't read stargazer lists (GitHub returns `403 Resource not accessible by personal access token`), and the momentum metric depends on them. A classic token with no scopes can only read public data.

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
