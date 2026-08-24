<!-- Derived by evaluator.py (openai/gpt-oss-120b) from the live assessment for Full Stack Product Engineer. Hand edits are preserved; delete this file or pass --force-rubric to regenerate. -->

## What this assessment tests  
A strong hire must demonstrate the ability to take an open‑ended product brief, decide what to ship within a tight timebox, and deliver a working full‑stack feature set with clear product judgment.  They also need to show disciplined engineering practice (tests, docs, deployment) and mature, transparent use of AI‑assistance.

## What each category means here  

- **Problem understanding** – Does the candidate answer the *specific* prompt (lightweight collaborative editor) rather than a generic “build a text editor”?  Look for evidence that they identified the required core capabilities, the time‑box constraints, and the need to prioritize scope.  
- **Depth of reasoning** – Are the trade‑offs (e.g., rich‑text library vs custom implementation, storage choice, sharing model) explicitly discussed?  Does the answer weigh alternatives, explain why a particular approach was chosen, and anticipate consequences (performance, security, future work)?  
- **Domain craft** – Shows concrete full‑stack product engineering skill: selecting a front‑end editor component, designing a REST/GraphQL API, persisting formatted content, implementing a minimal sharing ACL, writing at least one automated test, and producing a deployable build.  The rubric expects concrete code‑level decisions, correct handling of rich‑text markup, file‑upload handling, and a functional UI that a reviewer can use.  
- **Practical execution** – Provides a runnable repo with clear `README`, a live URL, seeded user credentials, and step‑by‑step instructions.  Includes a working feature list, a short architecture note, an AI‑workflow note, and a walkthrough video.  Anything missing or only described in theory is a penalty.  
- **Communication** – Information is organized (README, architecture note, AI note, SUBMISSION.md) and written concisely.  The video follows the required outline, and the submission folder is easy to navigate.

## What a strong answer does  

- Supplies a **live deployment link** that lets a reviewer create, edit, rename, and reopen a document with bold/italic/underline/headings/lists intact.  
- Includes a **README** that lets anyone clone the repo, run `npm install && npm start` (or equivalent) and log in with the provided seeded accounts to test sharing.  
- Implements **file upload** that accepts at least `.txt` or `.md`, converts the content into an editable document, and clearly states supported types in the UI/README.  
- Provides a **simple sharing UI** where the owner can add another seeded user by email/username, and the shared user can see the document listed under “Shared with me”.  
- Persists documents, formatting, and sharing data in a **stable store** (e.g., SQLite, Postgres, or Supabase) and demonstrates that data survives a browser refresh or server restart.  
- Contains **≥1 automated test** (unit or integration) that validates a core piece such as document‑save‑load or sharing permission enforcement.  
- Supplies a **short architecture note** that explains the chosen tech stack, why the rich‑text library was selected, storage decisions, and the prioritization trade‑offs made under the 4‑6 h limit.  
- Provides an **AI‑workflow note** that lists the exact tools (e.g., Claude, GitHub Copilot), what was generated, what was edited or rejected, and how correctness was verified (code review, manual testing).  
- Includes a **3‑5 min walkthrough video** that walks through the main flow, points out what was deliberately left out, and highlights the key implementation decisions and AI assistance.  
- Clearly states in `SUBMISSION.md` any **partial features**, what is working, what is incomplete, and a concrete 2‑4 h plan for the next steps.

## What a weak answer does  

- Submits only a code archive with no live URL or the URL is broken/unreachable.  
- Provides a README that is missing setup steps, credentials, or does not explain required environment variables.  
- Implements the editor but lacks any **rich‑text formatting** or the formatting does not persist after reload.  
- Claims sharing works but offers no UI, no seeded users, or the permission check is missing/always‑allow.  
- Uses an external paid service (e.g., paid Supabase tier) without providing a free alternative or credentials, forcing reviewers to pay.  
- Mentions AI tools but gives no concrete note, or the note lists many tools without indicating what was actually used or how correctness was ensured.  
- Omits automated tests, architecture documentation, or the walkthrough video is absent or does not cover the required points.  

## Score bands  

- **90‑100** – All core features work flawlessly, deployment is accessible, documentation is complete, AI‑workflow is transparent, and the candidate shows thoughtful prioritization with clear trade‑off rationale.  
- **75‑89** – Most core features work; minor bugs or missing edge‑case handling exist, but the product is usable, documentation is mostly complete, and reasoning is solid.  
- **60‑74** – Core editing works but one or two required capabilities (e.g., file upload or sharing) are incomplete or only stubbed; documentation or AI note is thin, but the overall direction and reasoning are evident.  
- **40‑59** – Significant portions missing (e.g., no persistence, no live demo, or no sharing), or the solution is mostly a design sketch; documentation is poor, and little evidence of product judgment.  
- **0‑39** – Submission does not run, lacks most required deliverables, shows little understanding of the problem, and provides no usable product or reasoning.