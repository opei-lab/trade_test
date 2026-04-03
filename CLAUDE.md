# CLAUDE.md

## Communication

- Respond in Japanese. Code/variable names in English
- Be concise. Skip obvious explanations
- Complex tasks: present plan → get approval → implement

## Workflow (IMPORTANT)

- Explore → Plan → Implement → Verify. Never skip Plan for non-trivial tasks
- Plan must include: scope (what to do / what NOT to do), files to change, risks
- Use `think hard` for complex architectural decisions
- Verify: run lint, typecheck, tests after implementation
- Small fixes (typo, 1-line change): skip planning

## Rule Evolution

- When user gives a reusable instruction → ask "Add this as a standard rule?"
- YES → add to appropriate file under `docs/rules/` (NOT directly to CLAUDE.md)
- Keep CLAUDE.md minimal. Each line must justify its per-session token cost
- Do not write what linters enforce or what is obvious from code

## Prohibitions

- Do not auto-commit or auto-push without explicit request
- Do not refactor working code without a stated reason → ask first
- Do not generate/modify docs unless asked
- Do not delete test code without confirmation → ask first
- Do not guess library APIs → verify via official docs (WebFetch/WebSearch)

## Detailed Rules (@imports)

@docs/rules/git.md
@docs/rules/coding.md

## Essential Commands

```bash
# Start app
streamlit run app.py
# Docker
docker-compose up -d
# Or just double-click: start.bat
```

## Project Overview

グロース市場に絞り、低価格帯かつボラティリティの高い銘柄に厳選するスクリーニングシステム。下値が限定的で上値が大きい非対称リターン構造の銘柄をメインに、IR・需給・テーマ・大口の動きなど直近のあらゆるシグナルを検知し、高確度で約定に貢献する。

- Target: Growth market, <¥5,000, 60-day range >30%
- Filter: Bottom zone × volume dry-up × upside >50% × RR >3x
- Analysis: IR/TDnet, sector win patterns, theme maturity, whale detection, algo phase
- Operation: Fully automated watchlist (add/track/remove). No manual intervention
- Exit: Take profit at catalyst (licensing/commercialization). Sell all if goal unclear (relay strategy)
- Local execution (Docker ready). No API keys. Simulation only (no real trading).

## Architecture

- **UI**: Streamlit (dark theme, multi-page)
- **Data**: yfinance (price) / EDINET (holders) / TDnet (IR)
- **Analysis**: supply scoring / manipulation phase detection / theme momentum
- **DB**: SQLite (price cache, recommendations, feedback loop)
- **Self-improving**: Ollama (local LLM) + auto feedback loop (Phase 3+)
