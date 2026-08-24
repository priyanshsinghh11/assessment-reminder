<!-- Derived by evaluator.py (openai/gpt-oss-120b) from the live assessment for Director of IT / CISO. Hand edits are preserved; delete this file or pass --force-rubric to regenerate. -->

## What this assessment tests  
A strong hire must demonstrate the ability to **analyze a real‑world multi‑cloud environment, spot the highest‑impact security gaps, and prescribe concrete, cloud‑native remediations**. They also need to **apply incident‑response, compliance (HIPAA/FERPA/SOC 2), vendor‑risk, RBAC, and AI‑risk governance** in a way that is immediately actionable for a fast‑moving AI consultancy.

---

## What each category means here  

- **Problem understanding** – The answer addresses **exactly the prompts** (risk ranking, network redesign, 60‑minute IR playbook, breach‑notification analysis, compliance program comparison, policy gap audit, vendor‑assessment checklist, RBAC matrix, AI‑workflow & governance). Markers look for evidence that the candidate read the environment description, noted the PHI/FERPA constraints, and kept the 90‑day deadline in mind.  

- **Depth of reasoning** – The response goes beyond “do X”. It explains **why** a risk is most severe, weighs trade‑offs (e.g., private‑service‑connect vs. Cloud SQL proxy), cites relevant standards (HIPAA §164.308(a)(1)(ii)(A), FERPA 34 CFR §99.31, SOC 2 CC6.1), and shows the logical flow that leads to each recommendation.  

- **Domain craft** – The candidate shows **hands‑on knowledge of GCP and Azure security services** (IAM custom roles, Cloud KMS, Secret Manager, VPC Service Controls, Private Link, Azure AD Conditional Access, Azure Policy, AKS pod security standards, Sentinel, etc.) **and of compliance frameworks** (HIPAA breach‑notification, FERPA data‑sharing rules, SOC 2 trust‑service criteria). The rubric expects concrete service names, configuration details, and correct regulatory citations.  

- **Practical execution** – The answer is a **ready‑to‑implement plan**: numbered steps, exact CLI/console actions, required IAM bindings, retention periods, monitoring alerts, and sample policy snippets. It includes **timelines, owners, and measurable outputs** (e.g., “rotate all service‑account keys within 48 h”, “enable VPC‑SC for the ‘prod‑vpc’ by day 15”).  

- **Communication** – The submission is **well‑structured, uses headings, tables or matrices where appropriate, and is concise**. Each bullet can be checked “yes/no” without hunting for the information.  

---

## What a strong answer does  

1. **Ranks five risks** and for each provides a remediation that names a specific GCP or Azure service (e.g., “replace Owner‑level service‑account with least‑privilege custom role via IAM → Custom Role”, “move Cloud SQL to private IP and enable Private Service Connect”).  
2. **Draws a target‑state network diagram** (or textual equivalent) that includes: a custom VPC with subnet segregation, Cloud‑SQL private IP, Azure VNet peering with Private Link, and a federation design using Google Identity‑Aware Proxy ↔ Azure AD SAML.  
3. **Lists a 60‑minute IR timeline** with ordered actions (contain, evidence‑preserve, revoke key, enable Cloud Audit Logs, notify CISO, start forensic imaging) and explains why the service‑account incident is prioritized over the stolen laptop, then adds a separate laptop‑theft SOP.  
4. **Cites the exact HIPAA breach‑notification clause** (45 CFR §164.308(a)(1)(ii)(A)) and FERPA breach‑determination guidance, states the burden of proof (“reasonable belief of unauthorized PHI disclosure”), and gives a clear go/no‑go recommendation.  
5. **Writes a CEO‑facing response** that references the regulatory obligations, potential penalties, and the business‑risk rationale for full disclosure.  
6. **Provides a side‑by‑side matrix** of HIPAA, FERPA, and SOC 2 controls, highlights overlapping requirements (risk assessment, access control, encryption at rest) and divergent ones (PHI definition, student‑record consent), and recommends a unified “core controls” set plus three supplemental modules.  
7. **Delivers a one‑page memo** that correctly identifies PHI (any individually identifiable health information, including symptom descriptions when linked to a patient identifier) and explains why the legal team’s “PII‑only” view is wrong under the HIPAA Safe Harbor.  
8. **Identifies every compliance gap** in the draft policies, referencing the exact regulation (e.g., HIPAA §164.308(a)(1) – evidence preservation missing; FERPA 34 CFR §99.31 – student‑record classification error). Highlights the mis‑classification of PHI as merely “Confidential” (should be “PHI – Highly Sensitive”).  
9. **Creates a vendor‑assessment questionnaire** that asks for SOC 2 II audit report, AWS Artifact evidence, penetration‑test scope, data‑retention policy, and BAA language; and explains why “integration already built” is insufficient (needs risk‑acceptance sign‑off).  
10. **Presents a complete RBAC matrix** (roles: Security‑Engineer, Dev‑Ops, Developer, Analyst, Executive) with permissions per system (GCP IAM, Azure RBAC, GitHub, Google Workspace) and shows least‑privilege mapping.  
11. **Describes three AI‑enabled security tasks** (e.g., “log‑anomaly clustering with Vertex AI → Python script → JSON alert”, “policy‑as‑code linting with OpenAI Codex → PR comment”, “cloud‑asset inventory diff using Claude → diff report”) with tool names, inputs/outputs, trust boundaries, and quantified time savings.  
12. **Builds an AI risk register** with at least five rows (e.g., “LLM data exfiltration via prompt injection”, “model‑output hallucination causing false alerts”, “unauthorized third‑party API usage”, “vendor‑license compliance drift”, “developer copying PHI into public LLM”) showing likelihood, impact, current control, and mitigation.  
13. **Outlines an AI‑tool governance framework** that defines approval workflow (security review → legal → procurement), risk‑tier classification, data‑classification tagging rules, automated usage monitoring (Cloud Logging + Sentinel), and a non‑blame communication plan (training, “security champion” program).  

All of the above are **explicit, checkable statements** that a reviewer can tick “present” or “missing”.

---

## What a weak answer does  

- Lists generic risks (“poor IAM”) without ranking or without naming the exact GCP/Azure service to fix them.  
- Provides a high‑level “build a private network” statement but no VPC design, subnets, or connectivity method.  
- Gives an unordered “call the team, rotate keys” IR plan with no timeline, evidence‑preservation steps, or prioritization rationale.  
- Mentions “HIPAA breach” but does not cite the specific regulation or explain the burden of proof.  
- Supplies a single paragraph that says “we need one compliance program” without a matrix, overlap analysis, or recommendation.  
- Points out that the policies are missing “something” but does not map each gap to the exact HIPAA/FERPA/SOC 2 requirement.  
- Supplies a vague vendor‑assessment “ask for SOC 2 report” without any follow‑up questions on data residency, penetration‑test scope, or BAA language.  
- Proposes “give everyone admin” as the RBAC model or provides a free‑form list of roles with no permission matrix.  
- Describes AI usage only as “I use ChatGPT to write scripts” with no tool name, input/output definition, or risk discussion.  
- Provides an AI risk register that merely repeats generic IT risks (phishing, ransomware) instead of LLM‑specific threats.  
- Omits the CEO response or writes a defensive “we don’t have to tell them” without regulatory justification.  

---

## Score bands  

| Score | Description |
|------|-------------|
| **90‑100** | The submission hits every bullet in the “strong answer” list, includes all required service names, regulatory citations, timelines, matrices, and a clear, well‑organized layout. Minor wording issues only. |
| **75‑89** | Most strong‑answer items are present and correct; a few minor gaps (e.g., one risk missing a specific service, or a brief IR step) but overall the plan is actionable and well‑structured. |
| **60‑74** | Several strong‑answer elements are present, but there are noticeable omissions (e.g., no vendor‑assessment questionnaire, incomplete RBAC matrix, or missing HIPAA citation). The answer is still usable after some clarification. |
| **40‑59** | The response covers the basics (identifies risks, mentions IR) but lacks depth, concrete services, regulatory references, or any structured matrices. Significant re‑work would be needed to make it operational. |
| **0‑39** | The answer is largely generic, does not address the specific prompts, provides no concrete remediation, no regulatory citations, and fails to demonstrate domain‑specific knowledge. |