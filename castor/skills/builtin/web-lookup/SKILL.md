---
name: web-lookup
description: >
  Use when the user asks about something the robot doesn't know from its local
  knowledge — facts, current events, how-to questions, product specs, news,
  definitions, or anything requiring up-to-date information from the internet.
  Also triggers on "search for", "look up", "find information about", "research",
  "what is the latest on", "google", "what does X mean", "how do I", "specs for".
version: "1.1"
requires: []
consent: none
tools:
  - web_search
max_iterations: 3
---

# Web Lookup Skill

Use this skill to answer questions that require current or external information.

## Steps

1. Call `web_search(query)` with a concise, targeted search query derived from the user's question
2. Review the returned results (title + snippet)
3. Synthesise a clear, grounded answer citing the most relevant result
4. If results are poor quality, try one follow-up search with a refined query

## Guidelines

- Keep queries short and specific — avoid full sentences
- Prefer authoritative sources (official docs, Wikipedia, reputable news)
- If no useful results found: say so honestly, don't fabricate
- Do NOT use this skill for questions about the robot's own sensors or status — use `get_telemetry` for those
- Cite your source: "According to [title]..."

## References

See `references/query-patterns.md` for query shaping tips and examples.

## Gotchas

- **Search ≠ browse** — `web_search` returns snippets only, not full page content; if the snippet is insufficient for a factual answer, say "I found a reference but can't confirm details without browsing — here's what I found: [snippet]"
- **Stale results** — search results can be 6–24h old; for truly real-time data (stock prices, live events) caveat with "as of [result date if available]"
- **Robotics specs** — for hardware like Feetech STS3215, LeRobot, OAK-D: the official docs and GitHub READMEs are the most reliable; avoid third-party summaries which are often outdated
- **Don't over-search** — one good query + follow-up is usually enough; 3 failed searches probably means the information isn't publicly available, not that the query needs more tries
- **Robot-specific questions** — "what is OpenCastor?" or "how does RCAN work?" should use local knowledge (trajectory/skill memory) first, not web search; web search is for external world knowledge

## Example

User: "What is a Feetech STS3215 servo?"
→ `web_search("Feetech STS3215 servo specifications")`
→ Summarise: torque, voltage, protocol, use cases

User: "What's the latest news on robot manipulation research?"
→ `web_search("robot manipulation research 2026")`
→ Synthesise top 2–3 results into a summary
