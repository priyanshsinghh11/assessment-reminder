<!-- Derived by evaluator.py (openai/gpt-oss-120b) from the live assessment for Senior AI Native Designer. Hand edits are preserved; delete this file or pass --force-rubric to regenerate. -->

## What this assessment tests  
A strong hire must demonstrate the ability to **scope, design, and ship a usable product slice** under tight time constraints, while making clear product‑level trade‑offs. They also need to show **full‑stack competence** (frontend UI, backend API, persistence, auth‑like sharing) and **thoughtful, disciplined use of AI tools** that accelerates work without compromising quality.

---

## What each category means here  

- **Problem understanding** – The candidate correctly identifies the core deliverables (document CRUD + rich‑text editing, file upload, simple sharing, persistence) and the constraints (timebox, lightweight scope, no‑pay services). Look for a clear statement of what will be built vs. what will be omitted and why.  

- **Depth of reasoning** – The answer explains *why* particular features, tech choices, and trade‑offs were made (e.g., picking a specific editor library, storage option, or authentication mock). It weighs alternatives, anticipates edge cases (concurrent edits, file‑type limits), and justifies prioritization decisions.  

- **Domain craft** – Shows concrete product‑engineering skill: implementing a functional rich‑text editor, handling file parsing/upload, building a sharing model, persisting data, writing at least one automated test, and producing a deployable build. The code should be organized, typed (if applicable), and follow best practices for the chosen stack.  

- **Practical execution** – The submission includes a runnable repo, clear README, live URL, seeded users/credentials, a short architecture note, AI‑usage note, and a walkthrough video. All listed artifacts are present, the app boots, and the core flows work end‑to‑end.  

- **Communication** – The written materials (README, architecture note, AI note, SUBMISSION.md) are well‑structured, concise, and easy for a reviewer to scan. The video walks through the product in the order the rubric expects and highlights trade‑offs and AI contributions.

---

## What a strong answer does  

- **Scoping statement** that lists exactly which of the five core tasks are fully implemented, which are partially done, and which are deliberately omitted with a brief “next‑steps” plan.  
- **Chosen tech stack** (e.g., React + TipTap, Node/Express, SQLite) explained with at least two concrete reasons (e.g., rapid prototyping, zero‑cost hosting).  
- **Rich‑text editor** that supports bold, italic, underline, headings, and lists; formatting persists after reload and is stored in a structured format (e.g., JSON or HTML).  
- **File‑upload flow** that accepts at least one of the allowed extensions, validates size/type, and either creates a new document from the file or attaches it to an existing doc, with a UI cue showing the result.  
- **Sharing implementation** that records an “ownerId” and a list of “sharedWith” user IDs, provides a UI to add a seeded user, and visually distinguishes owned vs. shared docs in the document list.  
- **Persistence** that survives server restarts (e.g., SQLite file, Supabase free tier) and includes migrations or schema definitions in the repo.  
- **Engineering quality**: a README with one‑click local setup, a live deployment link (Vercel/Render/Render‑free tier, etc.), at least one unit/integration test (e.g., creating a doc, uploading a file, sharing), and a 200‑word architecture note describing the most important trade‑offs.  
- **AI‑native workflow note** that lists the specific tools (e.g., Claude, GitHub Copilot, Cursor), the exact prompts or tasks where AI saved time, and a brief description of any AI‑generated code that was edited or rejected, plus how correctness was verified.  
- **Walkthrough video** (3‑5 min) that shows: creating a doc, applying formatting, uploading a file, sharing with another user, reloading to prove persistence, and a quick comment on what was deprioritized and why.  

---

## What a weak answer does  

- **Vague scope** – says “I built everything” without indicating which features actually work or are missing.  
- **Missing core feature** – any of the five required capabilities (rich‑text editing, file upload, sharing, persistence, or at least one test) is completely absent.  
- **No justification** – chooses a framework or storage solution without any reasoning or comparison to alternatives.  
- **Broken or incomplete flow** – the app runs but the document cannot be reopened, formatting is lost, or sharing does not enforce any access check.  
- **Poor delivery artifacts** – README is missing or does not let a reviewer start the app, live URL is dead, AI note is absent, or the video is under 30 seconds and does not cover the required flows.  
- **Over‑engineered or out‑of‑scope** – attempts to implement full Google‑Docs features (real‑time cursors, extensive permission matrix) at the expense of the core tasks, and the result is half‑finished.  

---

## Score bands  

| Band | Overall score | Typical characteristics |
|------|---------------|--------------------------|
| **90‑100** | Excellent | All five core capabilities fully functional, clean UI, persistent formatting, proper sharing, ≥1 solid automated test, clear architecture & AI notes, live deployment works, concise 3‑5 min video, and thoughtful trade‑off rationale. |
| **75‑89** | Good | Most core features work end‑to‑end; one minor area (e.g., limited file types, missing edge‑case test) is incomplete but the rest is solid; documentation and video are clear; AI usage is well‑explained. |
| **60‑74** | Satisfactory | At least three core capabilities are complete; the missing or partially‑done features are acknowledged with a next‑steps plan; documentation exists but may be rough; video covers basics; some reasoning present but shallow. |
| **40‑59** | Needs improvement | Only two core features work or many are half‑done; major gaps in documentation, testing, or deployment; little to no explanation of trade‑offs or AI usage; video is missing or does not demonstrate required flows. |
| **0‑39** | Unacceptable | Fewer than two core capabilities are functional; submission cannot be run or accessed; no documentation, test, or AI note; no evidence of product thinking or engineering discipline. |