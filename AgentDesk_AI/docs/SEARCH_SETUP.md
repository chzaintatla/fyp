# Web answers: search setup

"Who is the current CEO of Microsoft?", "weather in Karachi today", "what is photosynthesis". For questions like these the assistant
searches the web, reads short text snippets as data, and answers in 2-3 sentences with sources.

It never scrapes a website and never pretends to be a browser. It uses two official services:

| Service | Used for | What you set up |
|---|---|---|
| **Wikipedia** (official REST API, at most 1 request per second) | "what is / who was ..." questions | Put your e-mail in `backend\.env`: `WIKIPEDIA_CONTACT_EMAIL=you@example.com`. Wikipedia's policy asks for it, and it is sent in the User-Agent: `AgentDesk-AI/0.3 (Riphah FYP; you@example.com)`. Without it Wikipedia is not used. |
| **Tavily** (search API) | everything else: news, weather, prices, sports, "current ..." | Create an account at tavily.com, then run once: `backend\.venv\Scripts\python.exe backend\scripts\set_search_key.py` and paste the key at the hidden prompt. It is stored in Windows Credential Manager (`AgentDesk-search` / `tavily`), not in a file. `--remove` deletes it. |

## What leaves your PC
Only when you have answered **Yes** to "Answer questions with the cloud AI" (onboarding / Settings > Listening and speaking):
- your question text goes to Wikipedia and/or Tavily (to search);
- the question and the returned snippets (not whole pages) go to Groq (to write the answer, with `gpt-oss-20b`).
Commands for this PC are not affected. With the switch off, or in Local-only mode, nothing is sent and the assistant says so.

## Limits and behaviour
- Each answer prompt is kept under **1,500 tokens** (counted with the gpt-oss tokenizer, `o200k_harmony`; its vocabulary file is downloaded
  once by `tiktoken` from a public file host, no user data is sent).
- Results and answers are cached for **10 minutes** per question.
- If a service answers "too many requests" (HTTP 429, or Tavily 432/433) the assistant says **"Search is busy, try again in a minute."**
  and does not retry in a loop.
- Page text and snippets are treated as data: instructions inside them are ignored.
- `summarise <https://...>` still fetches that one page directly, identifying itself honestly as `AgentDesk-AI/0.3`.
