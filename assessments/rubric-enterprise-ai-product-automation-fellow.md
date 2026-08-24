<!-- Derived by evaluator.py (openai/gpt-oss-120b) from the live assessment for Enterprise AI Product & Automation Fellow. Hand edits are preserved; delete this file or pass --force-rubric to regenerate. -->

## What this assessment tests  
A strong hire must demonstrate the ability to **design a reusable, AI‑enabled workflow** for a real‑world business process, **articulate clear product scope** for an MVP, and produce **actionable documentation and mock‑ups** that another teammate could hand‑off to. The candidate also needs to show **practical AI‑tool usage** and communicate the solution concisely.

---

## What each category means here  

- **Problem understanding** – The answer identifies the exact pain point in Ajaia’s current CRM practice, names the primary user (e.g., sales rep, account manager), and explains why solving this specific problem creates measurable value.  

- **Depth of reasoning** – The response goes beyond “we’ll build a bot.” It weighs alternatives (e.g., rule‑based tagging vs. LLM extraction), discusses trade‑offs (accuracy vs. latency, automation vs. human oversight), and backs claims with logical arguments or data points.  

- **Domain craft** – The candidate shows concrete product‑management and automation expertise: defines concrete inputs (email bodies, calendar events, notes), concrete outputs (structured activity log, stage tags, reminder tasks), maps AI techniques to each step (entity extraction, intent classification, summarisation), and specifies realistic tools (e.g., OpenAI GPT‑4, Zapier, Google Workspace APIs).  

- **Practical execution** – The submission includes a **tangible artifact** (one‑page spec, workflow diagram, low‑fidelity mock‑up, AI‑usage note, video link) with enough detail that a teammate could start building the MVP the next day.  

- **Communication** – The materials are well‑structured, use headings, tables or diagrams where appropriate, avoid unnecessary jargon, and the video (or written summary) walks the reviewer through the whole solution in a logical order.

---

## What a strong answer does  

- ✅ States the problem in one sentence, names the primary user (e.g., “Account Executive”), and quantifies the current loss (e.g., “missed follow‑ups cost ~5 % of pipeline”).  
- ✅ Lists **all input sources** (inbound email, sent email, calendar events, CRM notes) and **all output artifacts** (activity timeline, stage label, next‑action reminder, summary email).  
- ✅ Provides a step‑by‑step workflow diagram that shows: data ingestion → LLM extraction → confidence scoring → automated tag creation → human review queue → reminder generation.  
- ✅ Clearly marks which steps are fully automated, which require a human “approval” screen, and why (e.g., low confidence < 80 % triggers review).  
- ✅ Defines a **Version‑1 MVP** with a minimal feature set (e.g., email parsing + stage tagging + daily reminder) and explicitly lists what is **out of scope** (e.g., phone‑call transcription, predictive win‑rate).  
- ✅ Identifies at least two concrete risks (LLM hallucination, data‑privacy compliance) and proposes mitigation (confidence thresholds, on‑premise model).  
- ✅ Sets measurable success criteria (e.g., 90 % of relevant emails tagged, 30 % reduction in missed follow‑ups after 4 weeks).  
- ✅ Includes a **single documentation asset** (e.g., a one‑page PRD) that contains: purpose, user story, inputs/outputs table, flow diagram, and MVP checklist.  
- ✅ Supplies a **low‑fidelity mock‑up** (wireframe of the reminder dashboard or the human‑review UI) that labels key UI elements.  
- ✅ Provides an **AI workflow note** that names the model (e.g., “GPT‑4‑Turbo via Azure OpenAI”), the prompt pattern, any post‑processing scripts, and clarifies which decisions were the candidate’s versus the AI’s.  
- ✅ Shares a **public Google‑Drive folder link** (view‑only) and an **unlisted YouTube video** link; the video runs ≤ 3 min and walks through problem → workflow → MVP → AI usage.  

---

## What a weak answer does  

- ❌ Gives a vague problem statement (“we need a better CRM”) without naming a user or quantifying impact.  
- ❌ Lists inputs/outputs in generic terms (e.g., “emails” and “reports”) but does not specify fields, formats, or how they are transformed.  
- ❌ Presents a high‑level description (“AI will read emails”) with no concrete steps, confidence handling, or human‑in‑the‑loop points.  
- ❌ Omits a clear MVP scope; either tries to include everything or provides no justification for what is left out.  
- ❌ Lacks any risk analysis or success metrics.  
- ❌ Supplies only a paragraph of text and no separate documentation asset, diagram, or mock‑up.  
- ❌ No AI‑usage note, or the note merely says “I used ChatGPT to write the answer” without linking it to product decisions.  
- ❌ Missing or private video link, or the Google‑Drive folder is not publicly viewable.  

---

## Score bands  

| Score | Description |
|------|-------------|
| **90‑100** | All five categories are exemplary: problem is precisely defined, reasoning is deep and trade‑off‑rich, domain‑specific AI workflow is fully fleshed out, concrete MVP and metrics are provided, all required artifacts (PRD, diagram, mock‑up, AI note, video, folder link) are present and polished. |
| **75‑89** | Strong in most categories; minor gaps such as a slightly vague risk section, a less detailed mock‑up, or a video that is a bit long, but overall the solution is complete, actionable, and shows solid product judgment. |
| **60‑74** | Meets the basics: problem, inputs/outputs, and a workable workflow are present, but depth of reasoning or domain craft is thin, MVP scope is vague, or one artifact is missing/under‑developed. Still demonstrates potential. |
| **40‑59** | Several categories are weak: problem statement is generic, workflow lacks clear AI/human split, MVP is undefined, or required artifacts are incomplete (e.g., no video or no documentation asset). Shows limited readiness for the role. |
| **0‑39** | Fails to address the core tasks: missing most deliverables, no clear problem or workflow, no AI usage, or submission is inaccessible. Does not demonstrate the required product or technical thinking. |