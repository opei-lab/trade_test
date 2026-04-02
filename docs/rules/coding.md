# Coding Rules

## Quality

- Follow existing code style. Do not introduce new conventions without asking
- Security: no hardcoded secrets, no injection vulnerabilities
- No unnecessary comments (only when logic is non-obvious)
- Error handling only at system boundaries (user input, external APIs)
- Prefer functional approach; minimize side effects
- Strict typing: `unknown` over `Any`

## Scope

- Do only what was requested. No "while I'm at it" improvements
- Do not mix refactoring into bug fixes
- Add docstrings/comments/type annotations only to changed code
- No speculative abstractions for hypothetical future requirements

## Refactoring

- When requested: incremental steps, not big-bang rewrites
- Present plan → step-by-step execution → verify tests pass after each step

## Verification

- IMPORTANT: before writing code using a library/API, verify usage via official docs (WebFetch/WebSearch)
- Check requirements.txt/package.json before assuming a library is available
- Do not invent APIs/methods. If uncertain → ask user

## Analysis Principles (Stock Screener)

- IMPORTANT: the highest conviction comes from FACTUAL stage changes that the market has not priced in yet
- Detect stage changes from IR/filings: revenue jump, new contract, phase advancement, regulatory approval, capital alliance
- Compare the FACT vs current price. If the gap is large = market is sleeping = highest conviction
- Also detect negative stage changes: warrant issuance, dilution, delisting risk
- All price targets and recommendations must be derived from quantitative data + official public information (IR, filings)
- Never use analyst opinions, expert forecasts, or subjective assessments
- IR/official disclosures are FACTS, not predictions. Use them to quantify impact:
  - Extract market size of the product/technology from IR and public sources
  - Compare market size vs current market cap → calculate gap = potential multiplier
  - Example: drug targeting $10B market, current market cap $100M → 100x theoretical ceiling
  - Assess blockbuster potential: annual revenue potential >$1B = blockbuster
- Justification must cite specific numbers: market cap, target market size, float ratio, volume anomaly
- IMPORTANT: valuation benchmarks differ by industry. Never apply uniform thresholds:
  - Biotech: pre-revenue is normal, value = pipeline potential vs market size, PER is meaningless
  - SaaS/IT: PSR (Price/Sales) is primary, 10x+ PSR can be justified for high-growth
  - Manufacturing: PBR (Price/Book) matters, <1.0 = undervalued is common
  - Finance: PBR is key metric, ROE correlation is strong
  - Retail/Food: PER 15-25x is typical, deviation = signal
  - Infrastructure/Utilities: dividend yield and stability, low volatility expected
  - Semiconductor/Cyclical: normalize earnings across cycle, peak PER is misleading

## Technical Analysis Rules

- IMPORTANT: only use LEADING indicators (precede price movement), never LAGGING (follow price)
- Allowed: volume surge, Bollinger band squeeze, volume profile vacuum, margin balance changes
- Prohibited: moving average crossovers, MACD, golden/dead cross, Ichimoku lagging span
- Any technical signal must show >90% hit rate in backtest with immediate effect (within days)
- If a signal only appears AFTER the price has already moved, it is useless for entry

## Historical Data Bias

- IMPORTANT: pre-2018 stock data reflects a different market (less algo, more retail-driven)
- Backtest results must weight recent 3-5 years (post-algo era) over older data
- 100x patterns from 2005-2015 are NOT reproducible in current market → adjust expectations
- Exceptions where old patterns still apply: micro-cap stocks under algo radar, binary biotech events, sudden new themes

## Testing

- When writing tests: define expected behavior first → confirm failure → implement → confirm pass
- Use sub-agents for independent code review when needed
- Never delete test code without confirmation
