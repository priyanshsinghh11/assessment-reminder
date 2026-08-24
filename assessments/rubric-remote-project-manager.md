<!-- Derived by evaluator.py (openai/gpt-oss-120b) from the live assessment for Remote Project Manager. Hand edits are preserved; delete this file or pass --force-rubric to regenerate. -->

## What this assessment tests  
A strong hire must show they can take an open‑ended product brief, decide what to ship in a tight timebox, and deliver a working full‑stack feature slice with solid engineering quality.  They also need to demonstrate disciplined use of AI assistants while keeping ownership of the final code and product decisions.

## What each category means here  

- **Problem understanding** – Did the candidate grasp the *specific* constraints (time limit, core feature set, lightweight scope, need for a live demo, AI‑usage note) and tailor their solution accordingly? Look for explicit mention of the required capabilities and any deliberate cut‑backs.  

- **Depth of reasoning** – Does the answer explain *why* particular trade‑offs were made (e.g., choosing a rich‑text library vs building one, persisting to SQLite vs a cloud DB, limiting file types)? Are alternatives considered and justified with product or engineering arguments?  

- **Domain craft** – Evidence of full‑stack product engineering skill: a functional UI for document creation/editing with formatting, file‑upload handling, a sharing model, persistence that survives refresh, at least one automated test, clear deployment (e.g., Vercel, Render), and an architecture note that reflects realistic product decisions.  

- **Practical execution** – Concrete, runnable artefacts: a repository with a `README.md`, a live URL, seeded user credentials, a working AI‑workflow note, a walkthrough video, and code that can be started and used without extra paid services.  

- **Communication** – The submission is organized (README, SUBMISSION.md, architecture note, AI note, video link) and each piece is concise, well‑structured, and easy for a reviewer to scan and verify.

## What a strong answer does  

- Provides a **live deployment URL** that lets a reviewer create, edit (with bold/italic/underline/headings/lists), rename, and reopen documents.  
- Implements **file upload** for at least one documented type (e.g., `.txt` or `.md`) and clearly states the supported formats in the UI/README.  
- Shows a **sharing flow** with an owner column, a UI to grant access to a seeded user, and a visual distinction between owned and shared docs.  
- Persists documents, formatting, and sharing data in a **stable store** (e.g., SQLite, Postgres, Supabase) so data survives page refresh and server restarts.  
- Includes a **README** with one‑click local setup, required env vars, and steps to run the app and the test suite.  
- Supplies **at least one automated test** (unit or integration) that validates a core piece such as document save‑load or sharing permission enforcement.  
- Contains a **short architecture note** that lists the tech stack, why each component was chosen, and the prioritization decisions made under the 4‑6 hour limit.  
- Provides an **AI‑workflow note** that lists the tools used (e.g., Claude, GitHub Copilot), what was generated, what was edited or rejected, and how correctness was verified.  
- Shares a **3‑5 min walkthrough video** that walks through the main user flow, highlights intentional deprioritizations, and explains key implementation choices.  
- Lists everything in a **SUBMISSION.md** file and includes any seeded user credentials needed for the sharing demo.

## What a weak answer does  

- Omits one or more core capabilities (e.g., no file upload, no sharing UI, or no persistence across refresh).  
- Provides only a local `npm start` command with no live URL or with a URL that requires paid services to run.  
- Lacks clear setup instructions, missing env‑var definitions, or a broken `README`.  
- Contains no automated tests or the only test is a placeholder that never runs.  
- Mentions AI tools but gives no concrete note, or the note is just “I used ChatGPT” without describing impact or verification.  
- The walkthrough video is missing, too short (<1 min), or does not demonstrate the required flows.  
- Architecture or trade‑off reasoning is absent; the candidate simply lists technologies without justification.  
- The UI is unusable (e.g., formatting buttons do nothing, document cannot be renamed, or sharing UI is non‑functional).

## Score bands  

| Band | Score | Description |
|------|-------|-------------|
| **Excellent** | **90‑100** | All core features fully functional, polished UI, live deployment, comprehensive README, ≥1 solid automated test, clear architecture & AI notes, concise walkthrough video, and thoughtful trade‑off rationale. |
| **Strong** | **75‑89** | Most core features work end‑to‑end; minor UI or persistence quirks; decent docs, at least one test, live URL, AI note present, video covers main flow, and reasoning is mostly clear. |
| **Adequate** | **60‑74** | Several core features present but some are partial or buggy; documentation enough to run locally but deployment may be missing or flaky; minimal testing; AI note superficial; video incomplete. |
| **Weak** | **40‑59** | Only a subset of core capabilities delivered; major gaps in persistence or sharing; poor or missing docs; no live demo; little or no testing; AI usage not explained; video absent or irrelevant. |
| **Insufficient** | **0‑39** | Barely any required functionality; cannot be run or evaluated; missing most deliverables; no evidence of product judgment or engineering quality. |