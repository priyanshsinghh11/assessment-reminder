<!-- Derived by evaluator.py (openai/gpt-oss-120b) from the live assessment for IT Manager. Hand edits are preserved; delete this file or pass --force-rubric to regenerate. -->

## What this assessment tests  
A strong hire must demonstrate the ability to **triage and contain security incidents quickly**, while simultaneously **architecting a scalable, AI‑native IT environment** that meets HIPAA, FERPA, and SOC 2 requirements.  The candidate must also show **judgment in prioritising work, using AI to automate operations, and communicating a coherent, actionable plan**.

---

## What each category means here  

- **Problem understanding** – The answer directly addresses every sub‑prompt (immediate response, short‑term fixes, long‑term prevention; architecture, IAM, compliance, AI‑driven workflows, ticketing, 30‑day plan).  Markers look for evidence that the candidate recognised the constraints (remote teams, sensitive data, rapid growth) and did not drift into a generic “IT manager” checklist.  

- **Depth of reasoning** – The response explains *why* a step is taken, weighs trade‑offs (e.g., centralised vs. federated identity, on‑prem vs. cloud logging), and cites realistic risks with mitigations.  It should show analysis rather than a flat list of actions.  

- **Domain craft** – Shows concrete **IT‑operations expertise** expected of an AI‑first manager: specific IAM role hierarchies, concrete security controls (e.g., secret‑rotation, device‑posture checks), compliance‑by‑design mechanisms, and concrete AI tooling (e.g., LLM‑powered ticket triage, observability agents).  The level of detail must match a senior IT manager (e.g., naming AWS IAM policies, Azure Sentinel, HashiCorp Vault).  

- **Practical execution** – Provides **actionable deliverables**: a step‑by‑step 0‑2 hr containment checklist, a diagram or structured description of the architecture, concrete SLA numbers, sample ticket‑routing rules, and a 30‑day Gantt‑style priority list.  If a prototype or code snippet is mentioned, a link is supplied.  

- **Communication** – The write‑up is well‑structured (clear headings, bullet lists, tables where appropriate), concise, and free of jargon that obscures meaning.  The video note (if referenced) is summarised in the text, and the AI‑workflow note is brief yet complete.

---

## What a strong answer does  

1. **Lists the exact first‑hour actions** (e.g., revoke the exposed API key, rotate secrets, block the key in the API gateway, notify the product owner).  
2. **Provides a short‑term remediation checklist** that includes permission audit commands (e.g., `gsutil iam get`, Notion sharing matrix), risk‑reduction steps, and a drafted incident‑communication email template.  
3. **Presents a high‑level architecture diagram (or ASCII schematic)** that shows cloud provider, identity provider (IdP), AI model hosting, data lake, and collaboration tools, with data‑flow arrows and trust boundaries.  
4. **Defines a granular IAM model**: e.g., “Security‑Engineer” role with `read:logs` + `write:secrets`, “Data‑Analyst” role limited to HIPAA‑scoped buckets, and a “Zero‑Trust” device‑posture policy enforced via Jamf/Intune.  
5. **Specifies three realistic security risks** (secret leakage, compromised remote device, AI‑tool supply‑chain attack) and pairs each with a concrete mitigation (automatic secret rotation, MDM‑enforced encryption, AI‑model provenance scanning).  
6. **Outlines a compliance enforcement plan** that maps HIPAA, FERPA, SOC 2 controls to technical controls (audit‑log retention, encryption‑at‑rest, role‑based access reviews) and operational processes (quarterly policy review, automated audit‑log alerts).  
7. **Describes three AI‑powered IT workflows** with tool names (e.g., LangChain‑based ticket triage, Splunk AI Ops for anomaly detection, Azure OpenAI onboarding assistant), step‑by‑step automation vs. human hand‑off, and quantitative impact (e.g., “reduce mean time to resolution by 30 %”).  
8. **Delivers a concrete 30‑day execution plan** that lists top‑3 priorities, a day‑by‑day audit schedule, and the first artefact to build (e.g., “Deploy IAM baseline and secret‑rotation pipeline by Day 7”).  

---

## What a weak answer does  

- Gives a generic “revoke the key” statement without specifying *how* or *who* does it.  
- Lists security controls or compliance frameworks without tying them to the described environment (e.g., mentions “PCI‑DSS” when only HIPAA/FERPA matter).  
- Provides only high‑level concepts (e.g., “use cloud IAM”) with no concrete role definitions or policy examples.  
- Omits the AI‑native element entirely or mentions AI tools without showing how they integrate into the workflow.  
- Supplies a vague 30‑day plan like “improve security” without concrete milestones, owners, or measurable outcomes.  
- Uses dense paragraphs with no headings or bullet structure, making the answer hard to scan.  

---

## Score bands  

| Band | Description |
|------|-------------|
| **90‑100** | Fully addresses every prompt, demonstrates deep reasoning, supplies concrete IAM policies, a clear architecture diagram, detailed AI‑workflow specs, and a realistic 30‑day plan; communication is crisp and well‑organized. |
| **75‑89** | Covers all major sections with mostly concrete details; a few minor omissions or shallow explanations, but overall shows strong judgment and practical steps. |
| **60‑74** | Answers most prompts but contains several generic statements, missing some concrete controls or AI‑specific details; reasoning is present but not fully fleshed out. |
| **40‑59** | Provides a high‑level overview with many gaps (e.g., no incident containment steps, no IAM hierarchy, no compliance mapping); reasoning is thin and execution steps are vague. |
| **0‑39** | Fails to address key parts of the assessment, offers only generic IT manager talk, lacks any concrete plan or AI‑native thinking, and is poorly organized. |