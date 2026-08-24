<!-- Derived by evaluator.py (openai/gpt-oss-120b) from the live assessment for Technical Program and Project Manager, AI Delivery. Hand edits are preserved; delete this file or pass --force-rubric to regenerate. -->

## What this assessment tests  
A strong hire must demonstrate the ability to **scope, design, and ship a usable product slice** under tight time constraints, while making clear product‑level trade‑offs.  They also need to show **full‑stack competence** (frontend UI, backend logic, persistence, and simple auth/sharing) and **mature AI‑assisted development** that improves efficiency without compromising quality.

---

## What each category means here  

- **Problem understanding** – The candidate identifies the *core* deliverables (document CRUD + rich‑text editing, file upload, basic sharing, persistence) and explains why other Google‑Docs features are out of scope.  Markers look for a concise problem statement, explicit scope limits, and a rationale that matches the constraints.  

- **Depth of reasoning** – The answer contains analysis of alternatives (e.g., editor libraries, storage options, auth models), weighs trade‑offs (speed vs. feature breadth, client‑side vs. server‑side rendering), and justifies every major decision with product impact or engineering risk.  

- **Domain craft** – The candidate shows concrete product‑engineering skill: a functional rich‑text editor, reliable file‑upload handling, a working sharing model, and a deployable full‑stack app.  Evidence includes clean code, appropriate framework choices, error handling, and at least one automated test that validates core behavior.  

- **Practical execution** – The submission is *actionable*: a live URL that reviewers can click, a README that gets the app running in ≤ 15 minutes, a short architecture note that explains the stack, and a walkthrough video that demonstrates the end‑to‑end flow.  All artifacts are organized and referenced in a `SUBMISSION.md`.  

- **Communication** – The materials are well‑structured, concise, and easy to scan.  Headings, bullet lists, and screenshots/GIFs guide the reviewer through setup, feature coverage, and the AI‑usage narrative.  Typos and vague prose are minimal.

---

## What a strong answer does  

- **Delivers a live deployment** that lets a reviewer create, edit (with bold/italic/underline/headings/lists), rename, and reopen a document.  
- **Persists all data** (content, formatting, ownership, sharing) in a documented store (e.g., SQLite, Postgres, Supabase) so a page refresh never loses state.  
- **Implements file upload** (at least one supported type) and shows the imported content in the editor or attaches the file to a document, with clear UI limits noted.  
- **Provides a working sharing flow**: an owner can grant access to a seeded user, the shared document appears in a “Shared with me” list, and edit permissions are enforced.  
- **Includes a complete README** that lists prerequisites, step‑by‑step local setup, deployment URL, and test credentials.  
- **Ships an architecture note** (≤ 300 words) that explains the chosen editor library, storage choice, auth mock, and why other features were cut.  
- **Shows at least one automated test** (unit or integration) that verifies a core path such as “document save persists formatting”.  
- **Contains an AI‑workflow note** that names the tools (e.g., Claude, GitHub Copilot), cites specific prompts, and describes what was edited or rejected after AI generation.  
- **Provides a 3‑5 min walkthrough video** that walks through creation → edit → upload → share, points out intentional deprioritizations, and highlights AI‑assisted steps.  
- **Uses screenshots or a short GIF** in the README to illustrate the UI for reviewers who cannot run the app immediately.

---

## What a weak answer does  

- **Omits one or more core capabilities** (e.g., no sharing, no file upload, or no rich‑text formatting).  
- **Relies on a local‑only build** with no accessible deployment link, forcing reviewers to spend excessive time setting up.  
- **Lacks persistence** – documents disappear after a refresh or formatting is lost.  
- **Provides vague or missing documentation** (no README, no setup instructions, or no architecture rationale).  
- **Shows no evidence of AI usage** or lists tools without describing impact, making the AI‑native requirement unfulfilled.  
- **Has broken or missing automated tests**, or the only test is a placeholder that never runs.  
- **Walkthrough video is absent, too short, or does not cover the required flows.**  

---

## Score bands  

| Band | Description |
|------|-------------|
| **90‑100** | All five core features work flawlessly, data persists, sharing is demonstrably secure, live URL is stable, README & architecture note are crystal‑clear, ≥ 1 solid automated test, AI‑workflow note is detailed, and the video walks through every required flow with clear commentary. |
| **75‑89** | Most core features are functional; one minor piece (e.g., a formatting option or a test) is missing or partially implemented. Deployment works, documentation is complete, AI note is present but less granular, and the video covers the main flows. |
| **60‑74** | At least three core capabilities are present and usable, but there are noticeable gaps (e.g., no file‑type validation, sharing UI is minimal, or persistence is flaky). Documentation is adequate but may lack some setup detail; AI note is superficial; video is present but skips a flow. |
| **40‑59** | Only one or two core features work; others are stubbed or missing. Deployment may be broken or require extensive configuration. Documentation is sparse, AI usage is undocumented, and there are no automated tests. |
| **0‑39** | Core deliverables are largely absent (no editor, no persistence, no sharing). Submission is unstructured, lacks a live link, has no README, and provides no evidence of AI‑assisted work or testing. |