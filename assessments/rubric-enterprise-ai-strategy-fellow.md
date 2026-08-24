<!-- Derived by evaluator.py (openai/gpt-oss-120b) from the live assessment for Enterprise AI Strategy Fellow. Hand edits are preserved; delete this file or pass --force-rubric to regenerate. -->

## What this assessment tests  
A strong Enterprise AI Strategy Fellow must be able to **design a concrete, reusable AI‑enabled workflow** for a real business process, clearly articulate the problem, user, data model, and product scope, and produce documentation that another operator could follow. The candidate also needs to show **product‑level judgment** about what to automate vs. what needs human oversight, and demonstrate **effective use of generative AI tools** in the design process.

## What each category means here  

- **Problem understanding** – The answer identifies the exact pain point in Ajaia’s current CRM practice, names the primary user (e.g., sales rep or account manager), and explains why solving this specific workflow creates measurable business value.  
- **Depth of reasoning** – The response goes beyond “we’ll use AI”; it weighs alternatives (e.g., rule‑based vs. LLM tagging), discusses trade‑offs (accuracy vs. latency, privacy vs. automation), and backs claims with logical arguments or simple quantitative estimates.  
- **Domain craft** – The candidate shows concrete CRM and AI‑product knowledge: correct terminology (pipeline stages, activity logging, reminder cadence), realistic data sources (email headers, body, meeting notes), appropriate AI techniques (entity extraction, summarization, intent classification), and an understanding of sales‑ops constraints (compliance, data residency).  
- **Practical execution** – The submission contains **actionable artifacts**: a step‑by‑step workflow description or diagram, a scoped V1 feature list, clear risk mitigations, measurable success criteria, and a usable documentation artifact (spec, playbook, or low‑fi mock‑up).  
- **Communication** – The answer is well‑structured, uses headings or bullet lists, avoids unnecessary jargon, and each claim can be traced to a specific part of the submission.

## What a strong answer does  

- Names the **primary user** (e.g., “account‑executive who manages 30‑50 active prospects”) and describes the **current broken process** (e.g., “information lives in disparate email threads, leading to ≥ 30 % missed follow‑ups”).  
- Lists **exact inputs** (email metadata, body text, meeting notes, CRM‑exported opportunity IDs) and **precise outputs** (structured activity record, stage tag, next‑action reminder, audit log).  
- Provides a **process flow** that shows:  
  1. Email ingestion via an API or forward‑to address,  
  2. LLM‑driven summarization & entity extraction,  
  3. Automated stage assignment + reminder generation,  
  4. Human validation screen for ambiguous classifications,  
  5. Notification delivery (Slack/CRM task).  
- Defines a **V1 scope** that includes only the core loop (email parsing, auto‑tag, reminder) and explicitly **excludes** advanced analytics, sentiment dashboards, or multi‑channel integration.  
- Identifies **top three risks** (PII leakage, classification error > 15 %, notification fatigue) and proposes concrete mitigations (data‑masking, confidence thresholds, throttling rules).  
- Sets **success metrics** (average follow‑up latency ↓ 20 %, missed‑follow‑up rate ↓ 50 %, user‑approval rate ≥ 80 %).  
- Supplies a **single, clear artifact** (e.g., a one‑page product spec with a labeled flow diagram and UI mock‑up) that a teammate could hand‑off to engineering.  
- Writes an **AI Workflow Note** that names the exact models/services used (e.g., “OpenAI GPT‑4 for summarization, Azure Text Analytics for entity extraction”), explains why they were chosen, and distinguishes AI‑generated decisions from human‑only steps.

## What a weak answer does  

- Gives a vague user description (“sales team”) and a generic problem statement (“they need better CRM”).  
- Lists inputs/outputs in high‑level terms only (e.g., “emails and notes → better CRM”) without specifying fields or formats.  
- Sketches a workflow that omits key stages (no ingestion method, no human review, no notification mechanism).  
- Proposes a V1 that tries to include everything (sentiment analysis, predictive scoring) without prioritizing core functionality.  
- Mentions risks only superficially (“privacy”) and provides no mitigation plan or quantitative thresholds.  
- Provides no measurable success criteria or only generic goals (“make it useful”).  
- Submits an artifact that is either missing, illegible, or unrelated to the workflow (e.g., a generic PowerPoint slide deck).  
- Leaves the AI Workflow Note empty or merely states “we used AI” without naming tools or explaining the decision process.

## Score bands  

- **90‑100** – Meets every strong‑answer bullet; workflow is complete, V1 is well‑scoped, risks and metrics are quantified, artifact is crystal‑clear, and AI usage is explicitly justified.  
- **75‑89** – Covers most strong bullets; minor omissions (e.g., one risk not detailed or metric loosely defined) but overall solid design and documentation.  
- **60‑74** – Satisfies basic problem definition and workflow sketch; several strong bullets missing (e.g., no clear success metrics or incomplete AI note).  
- **40‑59** – Shows understanding of the task but many weak‑answer characteristics dominate; vague user/problem, incomplete flow, missing scope or artifact, limited reasoning.  
- **0‑39** – Fails to address core deliverables; little to no problem articulation, no workflow, no artifact, and no evidence of AI‑native thinking.