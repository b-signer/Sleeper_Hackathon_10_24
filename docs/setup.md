# Setup: keys and secrets (T04)

You need two keys in a `.env` file in the project root. Run `cp .env.example .env`: **both team keys are already filled in** (committed on purpose for the hackathon, to be rotated afterwards). Nothing else is needed. `.env` is git-ignored. Keep it that way, and don't post the keys anywhere outside this private repo.

| Key | What it's for | Cost | Who has it |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Calls the AI models: luna for development, terra for the demo | Paid from prepaid credits | **One shared team key.** It's already filled in `.env.example` (committed on purpose for the hackathon, to be rotated afterwards) |
| `GITHUB_TOKEN` | Reads **other people's public repos** (the ones Sleeper analyzes): stars, forks, commits, releases, activity, READMEs. It isn't used for our own repo; pushing and pulling use your normal git login | Free | **One shared team token** (Roee's, classic, no scopes), filled in `.env.example`. The team shares its 5,000 requests per hour. If you hit rate limits, make your own (below) and put it in your `.env` |

## Your own GitHub token (only if the shared one runs out of requests)

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
