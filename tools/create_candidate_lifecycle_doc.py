from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.enum.style import WD_STYLE_TYPE


OUT = r"C:\Users\Priyansh Singh\Desktop\Ajaia\assessment-reminder\Candidate_Lifecycle_for_HR.docx"


def shade(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn('w:shd'))
    if shd is None:
        shd = OxmlElement('w:shd')
        tc_pr.append(shd)
    shd.set(qn('w:fill'), fill)


def borders(table, color='D9D9D9', size='6'):
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    b = tbl_pr.first_child_found_in('w:tblBorders')
    if b is None:
        b = OxmlElement('w:tblBorders')
        tbl_pr.append(b)
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        tag = 'w:' + edge
        el = b.find(qn(tag))
        if el is None:
            el = OxmlElement(tag)
            b.append(el)
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), size)
        el.set(qn('w:color'), color)


def keep_table_rows_together(table):
    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    repeat = OxmlElement('w:tblHeader')
    repeat.set(qn('w:val'), 'true')
    tr_pr.append(repeat)
    for row in table.rows:
        tr_pr = row._tr.get_or_add_trPr()
        cant = OxmlElement('w:cantSplit')
        tr_pr.append(cant)


def cell_text(cell, text, bold=False, color='000000', size=9.5):
    cell.text = ''
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.space_before = Pt(2)
    r = p.add_run(text)
    r.bold = bold
    r.font.name = 'Aptos'
    r.font.size = Pt(size)
    r.font.color.rgb = RGBColor.from_string(color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def table(doc, headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    borders(t)
    for i, h in enumerate(headers):
        cell_text(t.rows[0].cells[i], h, bold=True, color='FFFFFF', size=9)
        shade(t.rows[0].cells[i], '1F4E79')
        if widths:
            t.rows[0].cells[i].width = Inches(widths[i])
    for ridx, row in enumerate(rows):
        cells = t.add_row().cells
        for i, value in enumerate(row):
            cell_text(cells[i], str(value), size=9)
            if widths:
                cells[i].width = Inches(widths[i])
            if ridx % 2 == 1:
                shade(cells[i], 'F2F6FA')
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    keep_table_rows_together(t)
    return t


def bullet(doc, text, level=0):
    p = doc.add_paragraph(style='List Bullet' if level == 0 else 'List Bullet 2')
    p.paragraph_format.space_after = Pt(3)
    p.add_run(text)
    return p


def numbered(doc, text):
    p = doc.add_paragraph(style='List Number')
    p.paragraph_format.space_after = Pt(3)
    p.add_run(text)
    return p


def heading(doc, text, level=1):
    p = doc.add_heading(text, level=level)
    p.paragraph_format.keep_with_next = True
    return p


def para(doc, text, bold_lead=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.08
    if bold_lead and text.startswith(bold_lead):
        p.add_run(bold_lead).bold = True
        p.add_run(text[len(bold_lead):])
    else:
        p.add_run(text)
    return p


doc = Document()
sec = doc.sections[0]
sec.top_margin = Inches(0.7)
sec.bottom_margin = Inches(0.65)
sec.left_margin = Inches(0.75)
sec.right_margin = Inches(0.75)

styles = doc.styles
styles['Normal'].font.name = 'Aptos'
styles['Normal'].font.size = Pt(10.5)
styles['Normal'].font.color.rgb = RGBColor(0, 0, 0)
styles['Normal']._element.rPr.rFonts.set(qn('w:eastAsia'), 'Aptos')
for name, size, color in [('Title', 26, '000000'), ('Heading 1', 17, '000000'), ('Heading 2', 12.5, '000000'), ('Heading 3', 10.5, '000000')]:
    st = styles[name]
    st.font.name = 'Aptos Display' if name == 'Title' else 'Aptos'
    st.font.size = Pt(size)
    st.font.bold = True
    st.font.color.rgb = RGBColor.from_string(color)

# Word's built-in Title style can carry a theme-colored bottom border.
title_style_ppr = styles['Title']._element.get_or_add_pPr()
title_style_bdr = title_style_ppr.find(qn('w:pBdr'))
if title_style_bdr is not None:
    title_style_ppr.remove(title_style_bdr)

title = doc.add_paragraph(style='Title')
title.add_run('Candidate Lifecycle From Application To Hired')
title.alignment = WD_ALIGN_PARAGRAPH.LEFT
# Remove the built-in Word Title style border so the title remains plain black.
title_ppr = title._p.get_or_add_pPr()
title_bdr = title_ppr.find(qn('w:pBdr'))
if title_bdr is not None:
    title_ppr.remove(title_bdr)
sub = doc.add_paragraph()
sub.paragraph_format.space_after = Pt(14)
r = sub.add_run('HR operating guide for the assessment reminder, screening, evaluation and hiring dashboard')
r.italic = True
r.font.size = Pt(11)
r.font.color.rgb = RGBColor.from_string('4F4F4F')

para(doc, 'Purpose. This guide explains how a candidate moves through the current system from the moment they apply to the point where the dashboard records them as Hired. It is written for HR, recruiting and hiring managers who need to understand which steps are automated, which steps require human judgement, what each status means, and where the process ends.')
para(doc, 'Executive conclusion. The system is a funnel and decision-support tool, not a fully automated hiring engine. It imports candidate and assessment data, checks required artefacts, produces an AI-assisted assessment score and ranking, hands the strongest candidates to the assigned hiring manager, records interview outcomes, and preserves an auditable history. A candidate is not hired by the AI score alone: a human must invite the candidate, conduct or review the interview, and explicitly mark the candidate as Hired.')

heading(doc, 'The complete lifecycle at a glance', 1)
table(doc, ['Step', 'Candidate state', 'What happens', 'Primary owner'], [
    ('0', 'Role configured', 'Role, live assessment, assessment mapping and hiring managers are configured.', 'Recruiting'),
    ('1', 'Applied', 'Candidate applies in Workable. The application triggers the assessment invitation automation.', 'Candidate and Workable'),
    ('2', 'Invited or reminded', 'The system cross-checks Workable against the assessment portal and sends up to two reminders when eligible.', 'System and recruiting'),
    ('3', 'Assessment started or submitted', 'The portal stores the candidate record, answers, submission status and review status.', 'Candidate and portal'),
    ('4', 'Ingested', 'Recruiting runs Sync portal or ingest; roles, assessments and pending submissions are stored in MongoDB.', 'Recruiting'),
    ('5', 'Screened', 'Missing required video or resume is rejected before AI grading. Otherwise the candidate enters the grading queue.', 'System'),
    ('6', 'Evaluated', 'The assessment and, where available, the CV are scored against the role-specific rubric.', 'AI evaluator with human oversight'),
    ('7', 'Shortlisted', 'Scored, eligible candidates are ranked and handed to the assigned hiring manager.', 'Recruiting and hiring manager'),
    ('8', 'Interview', 'The hiring manager sends the invitation using their own words and booking link. Recruiting records the interview stage.', 'Hiring manager'),
    ('9', 'Hired or rejected', 'Recruiting records the final pipeline outcome and the system preserves the move history.', 'Recruiting'),
    ('10', 'Post-hire onboarding', 'Offer execution, payroll, background checks and onboarding occur outside this system.', 'HR and business'),
], widths=[0.45, 1.35, 4.15, 1.15])

heading(doc, '1. Step zero Role and process setup', 1)
para(doc, 'Before candidates arrive, each role must be connected to the correct live assessment. The role configuration contains the portal job identifier, assessment URL or mapping, role details, the scoring grid or the information needed to derive one, and the list of hiring managers. The hiring-manager list is both an ownership record and an access rule: managers see only roles to which they are assigned.')
bullet(doc, 'Recruiting owns role setup, portal sync, reminder operations, account management and the company-wide view.')
bullet(doc, 'A hiring manager can work only on their assigned roles. They can review and grade those candidates, send their role shortlist and manage their own booking link.')
bullet(doc, 'The role mapping must be validated before sending invitations. A wrong assessment mapping can send the wrong assignment to a candidate.')
para(doc, 'HR implication. If a manager cannot see a candidate or a role, first check the role assignment and account scope. The system deliberately does not expose other roles to a manager.')

heading(doc, '2. Step one Candidate applies and receives the assessment invitation', 1)
para(doc, 'The candidate applies through Workable. In the current design, the application is treated as the assessment invitation event because the Workable automation emails the assessment link when the candidate applies. Workable stage names are not a reliable indicator of whether the email moved a candidate to Assessment: some jobs remain in Applied even after the invitation is sent.')
table(doc, ['Source', 'Relevant information', 'Why HR cares'], [
    ('Workable', 'Candidate identity, job, application date and stage.', 'Defines the application cohort and role.'),
    ('Workable activity / automation', 'Assessment invitation is sent on application.', 'Explains why an applicant is eligible for reminders.'),
    ('Assessment portal', 'Candidate starts or submits the assignment.', 'Becomes the system of record for assessment work.'),
], widths=[1.2, 3.2, 2.7])
para(doc, 'The reminder process is separate from evaluation. Reminders are for applicants who have not started the assessment; evaluation is for candidates who have submitted it. These are two different queues and should not be confused.')

heading(doc, '3. Step two Reminder and follow-up process', 1)
para(doc, 'When Sync portal is run, or when the reminder command is run with automation enabled, the system performs three checks: it fetches candidates from the assessment portal, fetches recent eligible applicants from Workable, and cross-references the two sets. A candidate who appears in Workable but not in the portal is treated as not having started and may receive a reminder.')
table(doc, ['Eligibility rule', 'Current behavior'], [
    ('Application window', 'Applicants are limited to a recent 3 to 7 business-day window to prevent an old backlog from being emailed.'),
    ('Workable stage', 'Applied and Assessment are eligible. Sourced, Review, Failed Assessment and Talent Pool are excluded.'),
    ('Portal match', 'Anyone found in the portal is excluded from reminders, including candidates who have started or submitted.'),
    ('Reminder limit', 'Maximum two reminders per candidate, spaced two business days apart.'),
    ('Automation state', 'Automation is currently paused unless enabled in configuration; dashboard sync is a deliberate user action.'),
], widths=[1.55, 5.55])
para(doc, 'HR control. A reminder is not a rejection, a screen or an assessment result. It only means the system did not find a portal record for the eligible application cohort at the time of the scan.')

heading(doc, '4. Step three Candidate completes the assessment', 1)
para(doc, 'The assessment portal records the candidate submission, full answer text, submission status, review status, video link and resume link. The portal may show candidates in different review queues. The system ingests the untouched and Pending Review queues because those candidates are still awaiting a verdict. It does not normally ingest rejected, reviewed or interview queues for grading because those already carry a human decision.')
para(doc, 'The dashboard can show the candidate and their full submission, video and resume links. A candidate may be in progress, submitted, pending review or otherwise settled in the portal. The portal status answers whether the assessment was submitted; the review status answers whether a reviewer has touched it. They are not interchangeable.')

heading(doc, '5. Step four Sync and ingest into the dashboard', 1)
para(doc, 'A recruiting user runs Sync portal from the dashboard or runs the ingest command. The system downloads the live role and assessment data, fetches the relevant portal submission queues, and upserts the records into MongoDB. The ingest process owns portal fields only. It preserves internal evaluation results, manual decisions, pipeline stage, hiring-manager assignments and shortlist history so a later sync does not erase HR work.')
table(doc, ['Record area', 'Owned by', 'Survives a portal sync?'], [
    ('Candidate identity and assessment answers', 'Assessment portal', 'Refreshed from portal'),
    ('Resume text and fetch metadata', 'Resume fetch process', 'Preserved and updated when fetched'),
    ('AI evaluation and score', 'Evaluation system', 'Preserved'),
    ('Assessment decision status', 'Evaluation workflow', 'Preserved'),
    ('Interview, hired or rejected stage', 'Hiring pipeline', 'Preserved'),
    ('Hiring managers and shortlist sends', 'Role administration', 'Preserved'),
], widths=[2.25, 2.2, 2.65])

heading(doc, '6. Step five Screening before AI grading', 1)
para(doc, 'The first screen is an artefact check. If the candidate is missing a required video or resume, the system rejects the submission before sending it to the AI evaluator. This is a data-completeness decision, not a quality score. The rejection reason is distinct from a later human rejection after interview.')
para(doc, 'Resume text is fetched separately with the resume-ingest process. The system extracts text when possible and stores fetch metadata and errors. Private, deleted, invalid, folder-based or scanned files may not produce readable text. An unreadable CV therefore does not always mean the candidate has no experience; it means the evidence could not be used in the normal CV evaluation path.')
table(doc, ['Screen result', 'Meaning', 'Next step'], [
    ('Pending', 'Candidate has answer text and is waiting for grading.', 'Run Grade pending or Evaluate now.'),
    ('Rejected at screening', 'Required artefact is missing or unusable for the configured screen.', 'Recruiting may review the record and, where appropriate, return it to pending for re-evaluation.'),
    ('Scored', 'Candidate passed the initial artefact gate and has a usable evaluation.', 'Candidate can be ranked for shortlist review.'),
], widths=[1.45, 3.65, 2.0])

heading(doc, '7. Step six AI-assisted evaluation and ranking', 1)
para(doc, 'The evaluator reads the candidate assessment against a role-specific scoring grid. It performs triage checks, identifies fraud tells and auto-fail conditions, scores the assessment criteria, and produces evidence and a brief. Where a readable CV is available, it separately scores relevant experience, depth and progression, and skills match. The CV is blended with the assessment using the weight configured for that seat; the weight is not necessarily 50/50.')
bullet(doc, 'The assessment score and CV score are distinct. The CV is not supposed to lift the same assessment criterion a second time.')
bullet(doc, 'Fraud or an auto-fail can remove a candidate from the normal ranking, but disputed or hedged auto-fails are held for review rather than treated as fact.')
bullet(doc, 'A model response must be valid JSON. If it is malformed or incomplete, the system redraws the verdict and eventually returns a grading failure rather than saving an unreliable score.')
bullet(doc, 'The AI score is a recommendation and evidence summary. It does not move a candidate to Interview or Hired by itself.')
para(doc, 'What HR sees in the candidate drawer includes the scored grid, per-criterion marks and evidence, triage results, fraud tells, any auto-fails, the CV read, the full submission and the hiring-pipeline controls. A score should be read together with its evidence and artefact status, not as a standalone hiring decision.')

heading(doc, '8. Step seven Shortlist and manager handoff', 1)
para(doc, 'The shortlist is the set of scored candidates who are still awaiting a decision, are not artefact-rejected and have not already moved along the pipeline. The system ranks them strongest first. Recruiting assigns hiring managers to the role, reviews the candidate list and can preview or send the shortlist email.')
table(doc, ['Shortlist action', 'What the system does', 'Human responsibility'], [
    ('Assign managers', 'Controls who sees the role and who receives the shortlist.', 'Confirm the correct hiring owner and access.'),
    ('Review shortlist', 'Shows top candidates, evidence links and optional score visibility.', 'Check the ranking, evidence and candidate materials.'),
    ('Preview email', 'Renders the same content that will be sent.', 'Confirm recipients, message and links.'),
    ('Send shortlist', 'Emails each assigned manager their own copy and records the send.', 'Use the shortlist as an input to independent review, not as an automatic interview list.'),
], widths=[1.5, 3.6, 2.0])
para(doc, 'The shortlist email intentionally emphasizes candidate rank and links rather than exposing a bare score. The signed-in dashboard provides the score with the rubric output and evidence. This design gives the manager enough context to form an independent view.')

heading(doc, '9. Step eight Human review and interview invitation', 1)
para(doc, 'The hiring manager reviews the shortlist, assessment answers, video and CV. The manager decides who should be invited. The interview invitation is sent through the manager review workspace using the manager\'s name, message and booking link. The dashboard does not allow a recruiter or script to create an Interview stage directly through the normal pipeline route; the invitation is the manager\'s action and the system records its result.')
numbered(doc, 'Hiring manager opens the role shortlist and selects candidates worth meeting.')
numbered(doc, 'Hiring manager confirms or sets their booking link and writes the invitation message.')
numbered(doc, 'System sends the invitation and records whether the send succeeded.')
numbered(doc, 'Candidate books or attends through the manager\'s process. Interview time, interviewer and notes are recorded where provided.')
numbered(doc, 'The candidate appears on the Interview board, sorted by interview time. The drawer shows the invitation history and whether the email actually went out.')
para(doc, 'Important distinction. A candidate can appear in Interview even if an email later fails or if the meeting details are incomplete. HR should check the invitation history and not infer that an email was delivered solely because the stage is Interview.')

heading(doc, '10. Step nine Final decision Hired or Rejected', 1)
para(doc, 'After interview review, recruiting uses the candidate drawer or Interview board to mark Hired or Rejected. The action writes the pipeline stage and adds an event to the history. Removing a mistaken stage returns the candidate to the shortlist; it does not silently recreate an interview.')
table(doc, ['Dashboard stage', 'Meaning', 'What it does not mean'], [
    ('Shortlist / pipeline', 'Candidate has a usable evaluation and is awaiting an invitation or decision.', 'It is not an offer or a promise of employment.'),
    ('Interview', 'A hiring manager invited the candidate and the system recorded the stage.', 'It does not prove the interview occurred or that the email was delivered.'),
    ('Hired', 'An authorised user explicitly recorded the candidate as hired after review.', 'It does not create an employment contract, payroll record or onboarding task.'),
    ('Rejected', 'An authorised user explicitly rejected the candidate after review, usually after interview.', 'It is separate from artefact rejection and does not erase the evaluation.'),
], widths=[1.55, 3.55, 2.0])
para(doc, 'The candidate\'s evaluation remains available after a human pipeline decision. This allows HR to compare what the assessment predicted with what the interview decision produced, and to retain the history of movements such as booked, returned to shortlist, rebooked or rejected.')

heading(doc, '11. What happens after the Hired state', 1)
para(doc, 'The current system ends at the dashboard Hired state. It does not manage the complete employment lifecycle. The following activities should be handled in the organisation\'s HR, payroll or onboarding process: offer approval and signature, compensation and start date confirmation, background checks, right-to-work or compliance checks, employee master-data creation, payroll, equipment, access provisioning, onboarding tasks and probation review.')
para(doc, 'HR should therefore treat Hired as an internal pipeline outcome that triggers the next HR process, not as proof that the candidate has legally accepted an offer or started employment.')

heading(doc, '12. Statuses HR should understand', 1)
table(doc, ['Status or label', 'Plain-language meaning', 'Recommended HR action'], [
    ('In progress', 'Candidate started but has not submitted.', 'Do not grade; reminders may still depend on the portal match and Workable eligibility.'),
    ('Submitted / pending review', 'Candidate has completed the assessment and is waiting for a verdict.', 'Ingest and grade when the record is available.'),
    ('Pending', 'Dashboard evaluation queue is waiting for grading.', 'Run grading or assign the review task.'),
    ('Scored', 'Evaluation exists and candidate can be ranked.', 'Review evidence and decide shortlist treatment.'),
    ('Partial', 'Evaluation is incomplete or not comparable with a complete score.', 'Re-grade before using for ranking.'),
    ('Artefact rejected', 'Required video or resume is missing or unusable.', 'Review the reason; return to pending only if the record is genuinely recoverable.'),
    ('Interview', 'Hiring manager invitation was recorded.', 'Confirm meeting and review outcome.'),
    ('Hired', 'Recruiting recorded the final hiring pipeline outcome.', 'Start external offer and onboarding workflow.'),
    ('Rejected', 'Recruiting recorded a final negative pipeline outcome.', 'Follow the organisation\'s candidate communication policy.'),
], widths=[1.45, 3.8, 1.85])

heading(doc, '13. HR operating checklist', 1)
para(doc, 'Use this checklist when managing a role from opening to close.')
for item in [
    'Confirm the role is mapped to the correct live assessment and the correct hiring managers are assigned.',
    'Confirm the assessment invitation automation is active in Workable and the assessment link is correct.',
    'Use Sync portal to obtain the current candidate and submission data before reviewing or sending reminders.',
    'Treat reminders as follow-up only; do not use a reminder state as a screening decision.',
    'Run resume ingestion before grading when CV evidence is needed.',
    'Review artefact-rejected candidates separately from candidates rejected after interview.',
    'Read AI scores together with evidence, triage, fraud tells, CV availability and the role-specific weighting.',
    'Assign or confirm the role hiring manager before sending a shortlist.',
    'Have the hiring manager make the interview invitation through their own review workspace.',
    'Before marking Hired, confirm the human interview decision and record the outcome in the appropriate HR process as well.',
    'After marking Hired, trigger the external offer, compliance and onboarding workflow.',
]:
    bullet(doc, item)

heading(doc, '14. Controls and known boundaries', 1)
table(doc, ['Control or boundary', 'Why it matters to HR'], [
    ('Automation is paused unless explicitly enabled.', 'No assumption should be made that portal sync or reminders happen on a schedule.'),
    ('Sync is a deliberate action and sends can be guarded by data freshness.', 'Refresh before sending reminders or making a large communication decision.'),
    ('Portal-owned fields and HR-owned pipeline fields are separated.', 'A portal sync should not erase evaluations, hiring decisions or interview history.'),
    ('Managers are restricted by role assignment.', 'Access changes when the role hiring-manager list changes.'),
    ('AI grading fails closed when the response is malformed.', 'A 502 grading response means no reliable verdict was saved for that attempt.'),
    ('Interview entry is manager-controlled.', 'Recruiting cannot create an interview stage through the normal pipeline route without the manager invitation path.'),
    ('Hired is a pipeline stage, not an HRIS event.', 'Offer, legal employment and onboarding must be completed elsewhere.'),
], widths=[2.4, 4.7])

heading(doc, 'Final HR interpretation', 1)
para(doc, 'The process should be understood as four connected but separate lanes: candidate acquisition and reminders, assessment ingestion and screening, AI-assisted evaluation and shortlist ranking, and human hiring decisions. A candidate moves from one lane to the next only when the relevant evidence or human action exists. The most important governance rule is the final one: the system can rank and preserve evidence, but only an authorised human decision moves a candidate to Hired.')

footer = sec.footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
fr = footer.add_run('Candidate Lifecycle From Application To Hired | HR operating guide')
fr.font.size = Pt(8)
fr.font.color.rgb = RGBColor.from_string('6B7280')

doc.core_properties.title = 'Candidate Lifecycle From Application To Hired'
doc.core_properties.subject = 'HR guide to candidate movement from application through hiring'
doc.core_properties.author = 'Ajaia HR Operations'
doc.core_properties.comments = 'Prepared from the assessment reminder and evaluation dashboard implementation.'
doc.save(OUT)
print(OUT)
