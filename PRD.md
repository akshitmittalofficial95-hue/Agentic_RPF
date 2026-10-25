# Agentic RFP Evaluation and Supplier Ranking - PRD

## 1. Product Goal

Build an AI-assisted Streamlit application that helps a procurement team evaluate supplier RFP proposal PDFs consistently.

The application reads multiple supplier proposals, uses an LLM to produce evidence-grounded scorecards against active evaluation criteria, validates the model output, applies deterministic scoring and ranking rules, persists the complete run in SQLite, and presents an explainable leaderboard with downloadable JSON.

## 2. Business Problem

Procurement teams often receive several supplier RFP responses as PDFs. Manually reading each response and comparing suppliers against the same criteria is slow and inconsistent.

This product reduces manual effort by combining:

- document extraction
- LLM-based proposal evaluation
- strict validation
- transparent weighted scoring
- peer benchmarking
- deterministic tie-breaks
- persistent run history
- an interactive Streamlit user interface

## 3. Users

Primary user:

- procurement evaluator or instructor running an RFP comparison exercise

Secondary user:

- student/developer demonstrating an agentic AI workflow

## 4. Current Delivery Phase

The current phase is the classroom mini-project MVP.

The MVP must implement the complete local workflow:

1. load active criteria from SQLite
2. upload multiple supplier proposal PDFs
3. enter supplier metadata
4. create one RFP run
5. evaluate each supplier with an LLM
6. validate and normalize scorecards
7. calculate absolute scores, benchmarks, gaps, relative percentages, and PPI
8. rank suppliers with mandatory deterministic tie-breaks
9. store the complete result in SQLite
10. display leaderboard, scorecards, warnings, tie-break explanation, and JSON download

Do not add unrelated production features in this phase.

Out of scope for the MVP:

- multi-user authentication
- approval workflows
- production document storage
- external procurement system integration
- live collaboration
- background job queues
- monitoring
- payments
- real confidential supplier data

## 5. Functional Requirements

### Criteria Management

- Store evaluation criteria in SQLite.
- Load only active criteria for a run.
- Display active criteria, weights, and maximum score in Streamlit.
- Criteria weights for active criteria should total 100%.
- Criteria can be changed in the database without changing prompt code.

Default active criteria:

| Criterion | Weight | What The LLM Should Inspect |
|---|---:|---|
| Technical Capability | 30% | Architecture, integrations, scalability, technical fit |
| Implementation Plan | 20% | Timeline, milestones, staffing, risk plan |
| Commercial Value | 20% | Pricing clarity, total cost, assumptions |
| Security & Compliance | 20% | Controls, certifications, privacy, auditability |
| Support & Experience | 10% | Support model, similar projects, references |

### Supplier Input

- Allow multiple PDF uploads in one batch.
- For each supplier, collect:
  - supplier name
  - submission date
  - historical experience rating
  - proposal PDF
- Validate required metadata before evaluation.
- Create a unique `RFP_RUN_ID` for each evaluation batch.

The submission must include at least four artificial supplier PDFs:

| Supplier | Synthetic Profile |
|---|---|
| Apex Systems | Strong technical design and security; higher price; moderate delivery schedule |
| BrightPath Tech | Lowest price and fast timeline; weak compliance detail and limited experience |
| NexaWorks | Balanced proposal; strongest implementation plan and support model |
| Orbit Digital | Strong experience and references; vague integration plan; medium pricing |

Each artificial proposal should contain:

- executive summary and requirement understanding
- proposed solution and implementation approach
- timeline, team structure, and milestones
- price table with assumptions
- security, compliance, and risk controls
- support model, relevant experience, and references

### Document Extraction

- Extract readable text from each uploaded PDF.
- Treat extracted document text as the only source of supplier evidence.
- Surface extraction failures clearly.

### LLM Evaluation

- Evaluate each supplier independently.
- Prompt the model with:
  - supplier metadata
  - extracted proposal text
  - active criteria from SQLite
  - required JSON schema
- The model must return JSON only.
- The model must return one result for every active criterion.
- The model must use only evidence present in the supplier document.
- The model must cite supporting evidence for each criterion.
- The model may judge proposal content, but it must not calculate final arithmetic, benchmarks, tie-breaks, or ranks.

Expected LLM output shape:

```json
{
  "supplier_name": "Apex Systems",
  "criteria": [
    {
      "criterion_id": 1,
      "score": 8,
      "max_score": 10,
      "justification": "...",
      "evidence": "..."
    }
  ],
  "risks": ["..."],
  "overall_summary": "..."
}
```

### Validation

- Parse the LLM response as JSON.
- Validate the response with Pydantic.
- Ensure every active criterion has one scorecard item.
- Fill missing criteria with a safe default score and warning.
- Clip out-of-range scores to the criterion range and record a warning.
- Normalize malformed or missing justifications, evidence, risks, and summaries.
- Preserve validation warnings in the final run result.

### Scoring

Use deterministic Python for all formulas.

Absolute weighted score:

```text
sum((criterion_score / max_score) * criterion_weight)
```

Criterion benchmark:

```text
highest valid score observed for that criterion across all suppliers
```

Criterion gap:

```text
supplier_score - benchmark_score
```

Relative performance percentage:

```text
(supplier_score / benchmark_score) * 100
```

If benchmark score is zero, relative performance must use a safe deterministic rule and avoid division by zero.

Peer Performance Index:

```text
weighted average of criterion relative-performance percentages
```

### Ranking

Rank suppliers only after validation, scoring, and peer benchmarking.

Mandatory tie-break order:

1. higher PPI first
2. earlier submission date
3. higher historical experience rating
4. supplier name ascending

Assign final ranks as `1, 2, 3...` after the stable sort.

### Persistence

Persist complete run results in SQLite.

Minimum tables:

| Table | Minimum Fields |
|---|---|
| `evaluation_criteria` | `criterion_id`, `name`, `description`, `weight`, `max_score`, `is_active` |
| `rfp_runs` | `rfp_run_id`, `created_at`, `status` |
| `supplier_results` | `rfp_run_id`, `supplier_name`, `submission_date`, `experience_rating`, `absolute_score`, `ppi`, `final_rank`, `result_json` |

The stored `result_json` should include:

- supplier metadata
- normalized criterion scorecards
- warnings
- absolute score
- benchmark values
- criterion gaps
- relative percentages
- PPI
- final rank
- tie-break explanation
- LLM risks and overall summary

### Streamlit Screens

The application must include these sections:

| Section | Must Show |
|---|---|
| Criteria | active criteria, weights, maximum score |
| Supplier input | multiple PDF upload, supplier metadata, validation messages, Evaluate button |
| Leaderboard | rank, supplier, absolute score, PPI, submission date, experience rating |
| Detailed scorecard | criterion score, benchmark, gap, relative percentage, weight, evidence, justification |
| Run details | `RFP_RUN_ID`, warnings, tie-break explanation, JSON download |

## 6. Agentic Workflow

The orchestrator controls the workflow and calls tools in order.

Required components:

| Component | Responsibility |
|---|---|
| Orchestrator Agent | Controls the end-to-end run and calls required tools |
| Document Tool | Extracts clean text from uploaded PDFs |
| Evaluation Agent | Calls a JSON-capable LLM to score one supplier against active criteria |
| Validation Tool | Validates schema, fills missing criteria, clips invalid scores, records warnings |
| Ranking Tool | Calculates formulas, peer comparison, tie-breaks, and final ranking deterministically |

Important rule:

The LLM may evaluate proposal quality, but deterministic Python must own validation normalization, formulas, benchmark calculations, tie-breaks, persistence, and final ranks.

## 7. Non-Functional Requirements

- Same validated scorecards must always produce the same final formulas and ordering.
- Every final score must be traceable to a criterion, weight, supplier evidence, and business rule.
- The application should fail visibly when PDF extraction, LLM JSON parsing, validation, or persistence fails.
- The app should be simple enough to run locally and on Streamlit Community Cloud.
- Do not use real confidential supplier data.

## 8. Definition Of Done

The MVP is complete when:

- active criteria are loaded from SQLite and shown in Streamlit
- at least four artificial supplier PDFs can be uploaded and evaluated
- supplier metadata is validated
- the LLM returns criterion-wise JSON scorecards
- malformed, missing, or out-of-range LLM output is normalized with warnings
- absolute scores, benchmarks, gaps, relative percentages, and PPI are calculated correctly
- suppliers are ranked using the mandatory tie-break order
- complete results are stored under one `RFP_RUN_ID`
- leaderboard and detailed scorecards are visible in Streamlit
- complete results can be downloaded as JSON
- README, screenshots, sample exported JSON, and demonstration materials can be produced from the app

## 9. Submission Checklist

- complete source code with clear folder structure
- `requirements.txt`
- SQLite database creation and seed script
- at least four artificial supplier RFP PDFs
- deployed Streamlit Community Cloud URL
- README with setup steps, architecture, formulas, assumptions, and screenshots
- sample exported JSON for one completed RFP run
- short demonstration of one successful run and one validation or error case
