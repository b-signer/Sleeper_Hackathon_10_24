# Setup: keys and secrets (T04)

You need two keys in a `.env` file in the project root. Copy `.env.example` to `.env`, then fill it in. `.env` is git-ignored. Never commit it, and never paste keys into group chats, docs or issues.

| Key | What it's for | Cost | Who has it |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Calls the AI models: luna for development, terra for the demo | Paid from prepaid credits | **One shared team key.** Ask Roee for it by direct message or password manager |
| `GITHUB_TOKEN` | Reads public repo data from GitHub: stars, commits, READMEs. Without a token, GitHub allows only 60 requests per hour | Free | **Everyone makes their own**, because rate limits are per token |

## Make your GitHub token (2 minutes)

1. Go to github.com → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.
2. Name it `sleeper-hackathon` and set it to expire in 7 days.
3. Repository access: **Public repositories (read-only)**. Add no other permissions.
4. Generate the token and paste it into `.env` as `GITHUB_TOKEN=`.

## Spending safety

- The OpenRouter key has a **$50 limit**, set on the key itself, so calls fail once it's used up.
- Prepaid credits are a second cap. The account can never spend more than its balance.
- Use **luna** (the cheap model) for all development. Use terra only for demo runs.
- Check spending at any time: openrouter.ai → Activity.

## If a key leaks

Delete it straight away in OpenRouter (Keys) or GitHub (Developer settings), make a new one, and tell the team.

## Known machine issue

python.org's Python on macOS ships without SSL certificates (`CERTIFICATE_VERIFY_FAILED`). Use the project's `uv` environment, or run `/Applications/Python 3.x/Install Certificates.command` once.
