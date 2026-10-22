# Mini LLM Browser Agent (Playwright + gpt-oss-120b on Groq)

A small browser agent that completes a multi-site web task end to end: it finds a product price on one site, then fills a form on another, and stops before submitting.

**Demo:** `demo.webm` (screen recording) and `final_form.png` (end state).

## How it works
Observe → decide → act loop:
1. **Observe**: number every visible interactive element (label, type, options, *current value / checked state*) plus a short text excerpt.
2. **Decide**: the LLM returns one JSON action: `goto | click | type | select | check | extract | done`.
3. **Act**: Playwright executes it; the result is logged for the next turn.

## Reliability features (the interesting part)
Early versions failed in instructive ways, and each fix is a reliability pattern:
| Failure seen | Fix |
|---|---|
| Agent forgot the price once it left the recent-history window and looped between pages | Persistent **NOTES memory** of extracted facts, shown every turn |
| Repeated the same action forever | **Loop detection** that warns and skips repeats |
| Re-navigated to the page it was already on | Guard that blocks `goto` to the current URL |
| Extracted `£1.77` instead of `£51.77` (partial match) | **Exact extraction check**: value must appear as a standalone token on the page |
| Said "done" with a checkbox still unticked | **Verify-before-done**: required radios/checkboxes must read `CHECKED` from the DOM |
| Final summary misquoted the price | Summary built from **verified page state**, not model text |
| Groq 429s and connection resets | Retries with backoff / `retry-after` |

Safety: the agent can never click submit/pay/confirm buttons, and steps are capped.

## Run
```bash
pip install playwright requests && python -m playwright install chromium
export GROQ_API_KEY=...   # or put it in .env
python agent.py
```
