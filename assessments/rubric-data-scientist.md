<!-- Derived by evaluator.py (openai/gpt-oss-120b) from the live assessment for Data Scientist. Hand edits are preserved; delete this file or pass --force-rubric to regenerate. -->

## What this assessment tests  
A strong hire must show they can **structure a messy business problem**, quickly surface the most useful data insights, and design a realistic, production‑ready predictive workflow. They also need to demonstrate **hands‑on data‑science craftsmanship** (SQL/Python, feature ideas, model‑selection reasoning) and be able to **communicate the plan and impact** to senior non‑technical stakeholders.

---

## What each category means here  

- **Problem understanding** – The answer directly addresses the four tasks (exploratory plan, model design, stakeholder summary, AI‑workflow note). The candidate recognises the constraints (no raw data yet, 120‑min limit, class imbalance, need for actionable output) and tailors their work to the healthcare‑no‑show context rather than giving a generic “predict churn” solution.  

- **Depth of reasoning** – The response goes beyond “list features” or “run a model”. It explains *why* each question, query, feature, or metric matters, weighs trade‑offs (e.g., recall vs. cost of unnecessary reminders), and justifies methodological choices with reference to the summary statistics and business impact.  

- **Domain craft** – Shows concrete data‑science skill expected of a mid‑level Data Scientist at Ajaia:  
  - Correct, efficient SQL/Python snippets that join the three tables, handle time windows, and compute rates.  
  - Thoughtful feature engineering that leverages appointment lead time, provider‑level patterns, patient communication history, and external proxies (e.g., zip‑code socioeconomic data).  
  - Appropriate model family (e.g., gradient‑boosted trees) with a clear plan for handling the 22 % no‑show class imbalance, cross‑validation, and calibration.  
  - Realistic deployment sketch (batch scoring nightly, API endpoint for the operations dashboard).  

- **Practical execution** – The answer contains **specific, runnable code blocks**, concrete feature definitions, exact evaluation metrics (e.g., PR‑AUC, calibrated Brier score), a numeric decision‑threshold rationale, and a step‑by‑step rollout plan. Vague “we would do X” statements are penalised.  

- **Communication** – The stakeholder summary is a **single‑page, plain‑language brief** with bullet headings, clear business numbers, and a concise call‑to‑action. Overall writing is well‑structured, free of jargon, and easy for a VP of Operations to skim and act on.

---

## What a strong answer does  

1. Lists the **top 3–4 analytical questions** (e.g., “Which patient‑provider‑day combos have the highest historic no‑show rates?”) and explains why each drives the model.  
2. Provides **4–6 prioritized code blocks** (SQL or pandas) that:  
   - Compute overall and subgroup no‑show rates (by lead time, appointment type, insurance).  
   - Join `appointments` ↔ `patients` ↔ `communications` to get reminder delivery/open rates per appointment.  
   - Create a “historical no‑show count per patient (12 mo)” feature.  
   - Produce a provider‑level no‑show baseline.  
   - Sample a balanced training set using stratified sampling.  
3. States **clear, data‑driven hypotheses** (e.g., “Long lead times increase no‑show probability; confirmed SMS delivery reduces it”) and ties each to a specific query that will test it.  
4. Enumerates **10–12 engineered features** with rationale, marking which need extra data (e.g., zip‑code median income, weather on appointment day).  
5. Chooses a **modeling approach** (e.g., XGBoost) and explains why it handles mixed data types and non‑linear interactions, while also discussing at least one alternative (logistic regression, neural net) and why it was rejected.  
6. Details a **class‑imbalance strategy** (e.g., weighted loss, SMOTE, or undersampling) and a **validation scheme** (time‑based hold‑out + stratified k‑fold).  
7. Specifies **evaluation metrics** (PR‑AUC, recall at 80 % precision, calibration curve) and a **threshold selection** method (cost‑sensitive optimization using estimated $ per reminder vs. $ saved per avoided no‑show).  
8. Outlines a **deployment workflow**: nightly batch feature build, scoring API, UI flag in the scheduling system, and who receives the alert (operations manager, front‑desk staff).  
9. Delivers a **one‑page VP summary** that:  
   - Highlights the 22 % baseline no‑show cost ($4.2 M).  
   - Shows expected lift (e.g., 15 % reduction → $630 k saved).  
   - Describes the AI tool (risk score) and the simple daily workflow.  
   - Lists concrete client deliverables needed (raw tables, zip‑code mapping, reminder‑sent logs).  
10. Writes an **AI‑workflow note** that names the tools used (ChatGPT for brainstorming, GitHub Copilot for code snippets, pandas‑profiling for quick EDA) and distinguishes which decisions were human‑driven.

---

## What a weak answer does  

- Repeats the assessment prompt or gives generic “predict no‑show” without tailoring to the provided schema.  
- Lists many possible features but gives no justification, or includes only high‑level ideas (e.g., “age, gender”) without linking to the summary stats.  
- Supplies code that is syntactically incorrect, missing joins, or does not actually produce the described insight.  
- Mentions “use a model” but does not explain why that model fits, nor discuss class imbalance or validation.  
- Provides a stakeholder summary that is either too technical (lots of model jargon) or too vague (no numbers, no clear next steps).  
- Omits the AI‑workflow note or writes a generic statement like “I didn’t use AI”.  

---

## Score bands  

| Band | Overall score | Typical submission |
|------|---------------|--------------------|
| **90‑100** | Excellent | Meets every rubric bullet: crystal‑clear problem framing, deep, data‑backed reasoning, flawless SQL/Python, well‑justified feature set, robust model plan with imbalance handling, concrete deployment & threshold logic, concise VP brief, transparent AI‑tool note. |
| **75‑89** | Good | Covers all sections with mostly solid reasoning and working code; minor gaps (e.g., one feature lacking justification, or a less‑precise threshold method) but still highly actionable. |
| **60‑74** | Satisfactory | Addresses the tasks but with shallow reasoning, missing a few key code blocks or evaluation details; stakeholder summary is readable but lacks some business numbers; AI‑workflow note is minimal. |
| **40‑59** | Marginal | Shows basic awareness (some questions, a couple of queries) but many missing pieces: vague feature list, no class‑imbalance plan, unclear validation, or the VP summary is either too technical or too generic. |
| **0‑39** | Unsatisfactory | Fails to demonstrate problem understanding or domain craft; code is non‑functional, hypotheses absent, no model design, stakeholder brief missing or incomprehensible, and overall answer does not reflect a data‑science workflow. |