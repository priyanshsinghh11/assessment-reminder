<!-- Derived by evaluator.py (openai/gpt-oss-120b) from the live assessment for Operations Associate. Hand edits are preserved; delete this file or pass --force-rubric to regenerate. -->

## What this assessment tests  
A strong hire for the Operations Associate role must be able to **distill chaotic, real‑time executive information into concise, actionable deliverables**, prioritize competing demands on a CEO’s calendar, and design a repeatable, AI‑augmented workflow that balances speed with accuracy and confidentiality.

---

## What each category means here  

- **Problem understanding** – The answer shows that the candidate grasped the three distinct deliverables (synthesis doc, calendar/briefing packet, repeatable AI workflow) and the underlying constraints (single‑page output, no hallucinations, owners/deadlines required, flight delay, legal/compliance considerations).  

- **Depth of reasoning** – The response goes beyond “here’s a list.” It explains *why* a particular action item is assigned to a specific owner, why a meeting request is accepted or declined, and why a given AI step needs human sign‑off. Trade‑offs (e.g., speed vs. risk of hallucination) are identified and justified.  

- **Domain craft** – The candidate demonstrates the core skills of an Operations Associate: executive‑level summarisation, calendar optimisation, stakeholder alignment, and process design. Evidence includes correct use of terminology (e.g., “decision‑log”, “owner‑deadline matrix”), appropriate tools (calendar blocking, shared docs, AI note‑taking platform), and realistic timelines that match a CEO’s pace.  

- **Practical execution** – The answer is a ready‑to‑use artefact set: a one‑page synthesis document with the required sections, an updated calendar view showing exact time moves, a briefing packet outline with concrete bullet headings, and a step‑by‑step AI workflow that could be handed to the team tomorrow.  

- **Communication** – The submission is clearly structured (headings, bullet lists, tables), concise (no fluff), and easy to scan. Each claim is backed by a concrete example from the scenario, and the overall flow mirrors how a busy executive would read it.

---

## What a strong answer does  

1. **Delivers a single “Executive Synthesis” page** that contains:  
   - An **Executive Summary** of the leadership meeting in ≤ 3 sentences.  
   - A **Key Decisions** table listing each decision, the responsible function, and the agreed‑upon wording (e.g., onboarding definition = “six‑week total, three‑week implementation”).  
   - An **Action Items** table with Owner, Deadline (specific date, not “ASAP”), and a brief description.  
   - An **Open Questions / Unresolved Items** list that captures every “?” raised in the transcript.  
   - A **Risks** section that flags the dashboard timeline, travel‑cost visibility, and AI‑note‑taking hallucination risk.  

2. **Assigns owners and dates that match the CEO’s timeline** (e.g., “Mike – schedule engineering‑product sync for next week (by Tue Oct 3)”).  

3. **Shows no missing decisions** – every explicit “let’s do X” or “I need Y” from the transcript appears in the appropriate section.  

4. **Produces an updated calendar** that:  
   - Shifts the Tuesday flight 90 min later and moves all downstream events accordingly.  
   - Accepts the **Engineering urgent roadmap review** by moving it to Wednesday 8:30‑9:15 (pre‑board prep) and declines the **Prospective investor meeting** (conflicts with product workshop).  
   - Adds a **Legal compliance review** slot on Thursday 9:30‑10:15 with the CFO invited.  
   - Includes brief rationale notes next to each change (e.g., “Moved to preserve 30 min buffer before Board Prep”).  

5. **Creates a concise briefing packet outline for the Chicago trip** that lists, for each event (Investor Breakfast, School District Presentation, Partner Dinner):  
   - Background (one‑sentence context)  
   - Objectives (bullet list)  
   - Key People (names & titles)  
   - Prior conversations (link or reference ID)  
   - Top 3 questions the CEO should ask.  

6. **Designs a one‑page AI‑enabled daily workflow** that:  
   - Captures meetings via a chosen platform (e.g., Otter.ai) and auto‑generates a **Decision‑Log** using a prompt that extracts decisions, owners, deadlines, risks, and open questions.  
   - Routes the raw transcript to a **human reviewer** checklist (verify no hallucinations, add missing context).  
   - Pushes the vetted Decision‑Log into a shared “Executive Ops” Google Sheet that triggers Slack reminders for owners 24 h before deadlines.  
   - Generates a **Briefing Packet** template automatically populated from calendar events, email threads, and the Decision‑Log (using a simple Zapier/Make workflow).  
   - Includes explicit **confidentiality controls** (AI runs in a VPC, no data leaves the company, all outputs stored in encrypted drive, human sign‑off before any external distribution).  

7. **Provides clear reasoning for every prioritization choice** (e.g., “Legal review takes precedence over the investor call because a compliance breach would halt product rollout”).  

---

## What a weak answer does  

- Leaves out one or more required sections of the synthesis document (e.g., no Risks column or no Open Questions).  
- Provides vague owners/deadlines such as “Team X – soon” or “by end of week” without a concrete date.  
- Ignores the 90‑minute flight delay, leaving the original calendar unchanged.  
- Accepts or declines meeting requests without any justification or without checking for conflicts.  
- Supplies a briefing packet that is a paragraph of prose rather than a scannable, structured outline.  
- Describes the AI workflow only in abstract terms (“we’ll use AI to summarize”) and omits human‑review checkpoints, confidentiality steps, or concrete tool choices.  
- Contains spelling/formatting errors that make the document hard to skim quickly.  

---

## Score bands  

| Score | Description |
|------|-------------|
| **90‑100** | All three deliverables are present, perfectly aligned with the scenario, fully detailed, error‑free, and include clear rationales; the answer shows superior domain knowledge and a ready‑to‑implement workflow. |
| **75‑89** | Most sections are complete and accurate; minor omissions (e.g., a missing deadline or a brief justification) or small formatting issues are present, but the overall answer is actionable. |
| **60‑74** | Core deliverables are submitted but contain several gaps (e.g., missing owners, incomplete calendar adjustments, or a generic AI workflow); reasoning is present but shallow. |
| **40‑59** | Significant portions are missing or incorrect (e.g., no Risks section, calendar not updated for flight delay, or no concrete AI steps); the answer shows limited understanding of the role’s demands. |
| **0‑39** | Fails to deliver the required artefacts, misinterprets the task, or provides only high‑level commentary with no actionable content; demonstrates little to no grasp of the Operations Associate responsibilities. |