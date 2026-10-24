# T08 universe check

The last T08 step a person has to do: tick the rows below, fill in the two counts and sign. The header checks are automatic (`uv run python -m sleeper.universe_check check <universe>`), and both universes pass them. **T18 will rebuild both universes**, so after it merges, regenerate this sheet (same command with `--out` pointing at this file) and tick the new rows. This sheet is for the universes at `54659af`.

**Demo cache (done on this machine, 2026-10-24):** `uv run python -m sleeper.universe_check cache data/universes/*.json` reports 200/200 READMEs cached for each universe, plus the linked arXiv papers (6/6 and 3/3 repos). With `SLEEPER_OFFLINE=1`, a live run's GitHub and papers tools are served entirely from the cache; the model call still needs the network, so a run with no network at all is a replay. Citation counts are partly missing (Semantic Scholar rate-limited us), which S2 treats as unknown. **To finish:**
1. After T18's rebuild, run `uv run python -m sleeper.universe_check warm data/universes/*.json` again (it only fetches what's missing, and stops above 1,000 core requests left).
2. Copy the cache to the demo machine: `tar czf sleeper-cache.tgz .cache` here, then `tar xzf sleeper-cache.tgz` at the repo root there (about 230 MB).
3. On the demo machine: `uv run python -m sleeper.universe_check cache data/universes/*.json` should exit 0.

## Local-first software (`data/universes/local-first.json`)

- Built 2026-10-24 22:15 UTC by `sleeper 0.1.0 @ 54659af`, seed 7, max 200
- Found 1378; excluded archived 6, awesome_list 5, fork 11; eligible 1356; rows 200 (sampled)
- Stars 50-1729 (window 50-2000); created 2025-01-22 to 2026-10-18
- Queries: `topic:local-first stars:50..2000 created:>=2025-01-01 fork:true`; `local-first in:name,description stars:50..2000 created:>=2025-01-01 fork:true`

**Header issues:** 0

**Sample of 20 rows (seed 8).** Tick a row if it clearly belongs to the topic and its numbers look plausible on its GitHub page.

1. [ ] [eli-labz/Godcoder](https://github.com/eli-labz/Godcoder): 177 stars, Rust, created 2026-06-27. A local-first, open-source coding agent for your desktop. Bring your own LLM key; your code stays on your machine and only ever leaves to th
2. [ ] [Junfei-Z/clipbridge](https://github.com/Junfei-Z/clipbridge): 80 stars, JavaScript, created 2026-09-01. ClipBridge is a lightweight, local first bridge for sharing text and files across devices. ClipBridge 是一款轻量、本地优先的跨设备文字与文件传输工具。
3. [ ] [karant-dev/AutoRedact](https://github.com/karant-dev/AutoRedact): 82 stars, TypeScript, created 2025-12-11. 🛡️ Client-side, privacy-first image redaction tool. Automatically detects and blurs PII (Emails, IPs, Keys) using local OCR. No server, no d
4. [ ] [CCDawn/Vibelution](https://github.com/CCDawn/Vibelution): 80 stars, Python, created 2026-03-30. Local-first AI agent workbench: research teams that plan, build and review together, a finance agent, and virtual human companions — in one 
5. [ ] [dolthub/doltlite](https://github.com/dolthub/doltlite): 329 stars, C, created 2026-03-13. DoltLite - Version Controlled SQLite 
6. [ ] [tomtomsatnav/agent-orchestration-standard](https://github.com/tomtomsatnav/agent-orchestration-standard): 73 stars, JavaScript, created 2026-10-18. A live, local-first dashboard for Claude Code sessions: project graph of agents, tokens, tool feed, replay, alerts. Plugin + terminal UI + M
7. [ ] [airmang/python-hwpx-automation](https://github.com/airmang/python-hwpx-automation): 68 stars, Python, created 2025-09-19. MCP server for AI agents to read, edit, inspect, and validate local HWPX documents.
8. [ ] [arcboxlabs/arcbox-desktop](https://github.com/arcboxlabs/arcbox-desktop): 305 stars, Swift, created 2026-02-09. Native macOS GUI for ArcBox — containers, VMs, and sandboxes at your fingertips. Open Source alternative to OrbStack.
9. [ ] [chenkanglin198904/Personal_External_Brain](https://github.com/chenkanglin198904/Personal_External_Brain): 199 stars, Python, created 2026-09-23. Not another ChatGPT wrapper. This is a local-first tool that turns your notes, web pages, PDFs and audio/video into a personal knowledge gra
10. [ ] [f/agentlytics](https://github.com/f/agentlytics): 589 stars, JavaScript, created 2026-03-07. Comprehensive analytics dashboard for AI coding agents — Cursor, Windsurf, Claude Code, VS Code Copilot, Zed, Antigravity, OpenCode, Command
11. [ ] [mypaios/mypaios](https://github.com/mypaios/mypaios): 79 stars, Python, created 2026-07-27. My Personal AI OS — local-first chat, agents, video analysis, research & email triage on your own machine
12. [ ] [drakulavich/kesha-voice-kit](https://github.com/drakulavich/kesha-voice-kit): 79 stars, TypeScript, created 2026-04-06. Kesha listens and talks back: local speech to text and text to speech for agents and scripts, fastest on Apple Silicon.
13. [ ] [leanchy/BingoCode](https://github.com/leanchy/BingoCode): 103 stars, TypeScript, created 2026-04-26. BingoCode is a lightweight, open-source CLI orchestrator inspired by Claude Code. It encapsulates powerful AI-driven development capabilitie
14. [ ] [stffnb/edentext](https://github.com/stffnb/edentext): 554 stars, TypeScript, created 2026-06-18. Powerful web-based Word Processor for docx and odt.  Free, Open Source, Private by Design
15. [ ] [advai-x/advai-cli](https://github.com/advai-x/advai-cli): 261 stars, Python, created 2026-06-19. Local-first CLI for agent skills, browser automation, knowledge bases, external CLIs, and terminal AI chat.
16. [ ] [mateoroldos/md.uy](https://github.com/mateoroldos/md.uy): 55 stars, JavaScript, created 2025-05-02. Peer-to-peer markdown editor
17. [ ] [Monica-Pass/Monica](https://github.com/Monica-Pass/Monica): 1173 stars, Kotlin, created 2025-10-05.  Monica Pass，Password Management, 2FA Generator
18. [ ] [marswangyang/personal-ai-memory](https://github.com/marswangyang/personal-ai-memory): 69 stars, TypeScript, created 2026-02-24. A local-first Chrome extension that passively captures ChatGPT, Gemini, Claude, Grok, Perplexity conversations into a private memory graph. 
19. [ ] [kitfunso/hippo-memory](https://github.com/kitfunso/hippo-memory): 784 stars, TypeScript, created 2026-03-15. Stop re-teaching your agent. Make your agent's memory work like a brain. Hippo is long-term memory for coding agents. It's a critical layer 
20. [ ] [Mrbaeksang/deepcloak](https://github.com/Mrbaeksang/deepcloak): 60 stars, Python, created 2026-06-04. Local-first research tool with explicit fetch decisions, source evidence, and cited reports. CLI and MCP interfaces. Python, MIT.

On topic: __ / 20 (T18 needs at least 17). Checked by: ____

## Rust developer tools (`data/universes/rust-devtools.json`)

- Built 2026-10-24 22:22 UTC by `sleeper 0.1.0 @ 54659af`, seed 7, max 200
- Found 586; excluded archived 4, fork 4; eligible 578; rows 200 (sampled)
- Stars 50-1877 (window 50-2000); created 2025-01-05 to 2026-10-21
- Queries: `language:Rust topic:cli stars:50..2000 created:>=2025-01-01 fork:true`; `language:Rust topic:developer-tools stars:50..2000 created:>=2025-01-01 fork:true`; `language:Rust topic:devtools stars:50..2000 created:>=2025-01-01 fork:true`; `language:Rust topic:linter stars:50..2000 created:>=2025-01-01 fork:true`

**Header issues:** 0

**Sample of 20 rows (seed 8).** Tick a row if it clearly belongs to the topic and its numbers look plausible on its GitHub page.

1. [ ] [edison7009/Coffee-CLI](https://github.com/edison7009/Coffee-CLI): 207 stars, Rust, created 2026-04-08. A clean AI terminal workspace. No hacks, no extra hooks, no lock-in — with quick input, folder switching, copy & paste, and image pasting. 极
2. [ ] [kb019/sprout](https://github.com/kb019/sprout): 62 stars, Rust, created 2026-07-22. A habit tracker that lives in your terminal
3. [ ] [kitlangton/scrawl](https://github.com/kitlangton/scrawl): 728 stars, Rust, created 2026-10-14. Show an agent a problem: mark up, record, send
4. [ ] [charannyk06/conductor-oss](https://github.com/charannyk06/conductor-oss): 53 stars, Rust, created 2026-03-01. Local-first control surface for AI coding agents, workspaces, worktrees, terminals, diffs, previews, and paired-device access.
5. [ ] [Dicklesworthstone/franken_whisper](https://github.com/Dicklesworthstone/franken_whisper): 94 stars, Rust, created 2026-02-22. Agent-first Rust ASR orchestration stack: Bayesian backend routing across whisper.cpp/insanely-fast-whisper/whisper-diarization, real-time N
6. [ ] [utensils/mold](https://github.com/utensils/mold): 54 stars, Rust, created 2026-03-12. CLI-native local AI image and video generation for people, scripts, and agents — CUDA on Linux, Metal on macOS, plus desktop, web, iPhone, R
7. [ ] [Antoinegtir/flutter-cli](https://github.com/Antoinegtir/flutter-cli): 63 stars, Rust, created 2026-05-19. Modern terminal UI for Flutter — hot reload across N devices, live HTTP/perf inspector, drops into your shell so 'flutter run' becomes the d
8. [ ] [bitloops/bitloops](https://github.com/bitloops/bitloops): 244 stars, Rust, created 2026-02-28. Give AI coding agents the context they need to ship production-quality software.
9. [ ] [cioffivictor/ssp-protocol](https://github.com/cioffivictor/ssp-protocol): 110 stars, Rust, created 2026-04-20. STABLE - Stability protocol for Solana memecoins. A council of liquidity agents attests to pool state; a dependency-free Rust risk kernel tu
10. [ ] [elliotgao2/handsets](https://github.com/elliotgao2/handsets): 121 stars, Rust, created 2026-05-21. A high-performance Android automation CLI, built for agents and humans
11. [ ] [PantherApex/Panther](https://github.com/PantherApex/Panther): 67 stars, Rust, created 2026-03-05. A self-hosted AI agent daemon built in Rust. Runs on your machine, talks through Telegram, Discord, Slack, Email and Matrix. Fully local wit
12. [ ] [dpc/tau](https://github.com/dpc/tau): 106 stars, Rust, created 2026-04-17. Tau Coding Agent - like Pi, but twice as much
13. [ ] [LumaryLabsLLC/muxin](https://github.com/LumaryLabsLLC/muxin): 372 stars, Rust, created 2026-10-08. All your machines, one app. For you and your AI agents: agent sessions, desktops, files, logs and GitHub across your Macs, Linux and Windows
14. [ ] [StaszeKrk/plaza](https://github.com/StaszeKrk/plaza): 57 stars, Rust, created 2026-06-24. Cross-distro TUI package manager browser
15. [ ] [angleschina/angles-cli](https://github.com/angleschina/angles-cli): 56 stars, Rust, created 2026-07-20. Angles Code CLI — 2MB coding agent with 2GB powers
16. [ ] [nvrmnd-png/undo](https://github.com/nvrmnd-png/undo): 66 stars, Rust, created 2026-07-15. Undo mv, rm, cp and more from your shell. Journaled to SQLite, deletions go to the trash, nothing gets lost.
17. [ ] [ota-run/ota](https://github.com/ota-run/ota): 83 stars, Rust, created 2026-03-21. The execution contract for AI agents. Execution governance for software repositories, shared with humans and CI.
18. [ ] [nostacks/ekphos](https://github.com/nostacks/ekphos): 1073 stars, Rust, created 2025-12-15. A lightweight, fast, terminal-based markdown research tool inspired by Obsidian
19. [ ] [LambdaBytes/sping](https://github.com/LambdaBytes/sping): 128 stars, Rust, created 2026-09-30. A modern, cross-platform ping with real-time diagnostics, network context, quality scoring, and multi-target monitoring.
20. [ ] [owainlewis/push](https://github.com/owainlewis/push): 202 stars, Rust, created 2026-06-27. A secure, stable, and lightweight alternative to OpenClaw and Hermes.

On topic: __ / 20 (T18 needs at least 17). Checked by: ____
