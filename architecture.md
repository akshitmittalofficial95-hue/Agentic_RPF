# Agentic RFP Evaluation and Supplier Ranking - Architecture

## 1. Architecture Scope

This architecture describes a local Streamlit application for evaluating supplier RFP proposal PDFs.

The system combines:

- Streamlit UI
- SQLite persistence
- PDF text extraction
- JSON-capable LLM evaluation
- Pydantic validation
- deterministic scoring and ranking
- downloadable JSON results

The LLM is used only for evidence-grounded qualitative proposal evaluation. Final arithmetic, peer benchmarking, tie-breaks, persistence, and rank assignment are deterministic application responsibilities.

## 2. High-Level Flow

```text
Streamlit UI
  -> Criteria Repository
  -> Supplier Input
  -> RFP Run Orchestrator
  -> Document Tool
  -> Evaluation Agent
  -> Validation Tool
  -> Scoring Service
  -> Ranking Tool
  -> SQLite Repository
  -> Leaderboard, Scorecards, JSON Download
```

Required data flow:

```text
Setup
  -> load active criteria from SQLite
Input
  -> upload supplier PDFs and collect metadata
Batch
  -> create supplier entries and generate RFP_RUN_ID
Evaluate
  -> reload criteria, extract PDF text, build prompt, call LLM
Validate
  -> parse JSON and normalize missing or invalid criterion results
Score
  -> calculate absolute weighted score
Benchmark
  -> calculate best score per criterion and peer metrics
Rank
  -> calculate PPI, apply tie-breaks, assign final ranks
Persist
  -> write complete results to SQLite using one RFP_RUN_ID
Present
  -> show leaderboard, evidence, comparisons, warnings, and JSON download
```

## 3. Suggested Folder Structure

```text
.
|-- app.py
|-- requirements.txt
|-- README.md
|-- data/
|   |-- rfp_evaluation.db
|   `-- sample_outputs/
|-- scripts/
|   |-- init_db.py
|   `-- create_sample_pdfs.py
|-- samples/
|   `-- supplier_pdfs/
|-- src/
|   |-- agent/
|   |   |-- orchestrator.py
|   |   `-- evaluator.py
|   |-- db/
|   |   |-- connection.py
|   |   `-- repositories.py
|   |-- documents/
|   |   `-- pdf_extractor.py
|   |-- models/
|   |   `-- schemas.py
|   |-- scoring/
|   |   |-- calculator.py
|   |   `-- ranking.py
|   `-- validation/
|       `-- scorecard_validator.py
`-- tests/
    |-- test_validation.py
    |-- test_scoring.py
    `-- test_ranking.py
```

This structure is a recommendation. The important boundary is that document extraction, LLM evaluation, validation, deterministic scoring, persistence, and UI stay separate.

## 4. Component Responsibilities

| Component | Responsibility |
|---|---|
| Streamlit UI | Collect inputs, show criteria, display leaderboard and scorecards, provide JSON download |
| Criteria Repository | Load active criteria and validate active weight total |
| RFP Run Orchestrator | Coordinate the full evaluation run in the required order |
| Document Tool | Extract readable text from uploaded PDFs |
| Evaluation Agent | Ask the LLM for JSON scorecards using active criteria and supplier document text |
| Validation Tool | Parse, validate, normalize, and warn about LLM output issues |
| Scoring Service | Calculate absolute weighted scores and criterion-level metrics |
| Ranking Tool | Calculate PPI, apply tie-breaks, assign final ranks |
| SQLite Repository | Create runs and persist complete supplier results |

## 5. Agent and Tool Boundary

The application should use an agentic workflow, but each tool should keep one clear responsibility.

```text
Orchestrator Agent
  -> Document Tool
  -> Evaluation Agent
  -> Validation Tool
  -> Ranking Tool
  -> Persistence
```

### LLM-Owned Work

- assess proposal content against criteria
- produce criterion-wise score, justification, and evidence
- summarize risks and overall proposal quality
- return JSON only

### Deterministic Python-Owned Work

- PDF extraction plumbing
- criteria loading
- schema validation
- filling missing criteria
- clipping out-of-range scores
- warning generation
- absolute weighted score calculation
- benchmark calculation
- relative percentage calculation
- PPI calculation
- tie-break sorting
- final rank assignment
- SQLite persistence
- JSON export shape

The LLM must not decide the final arithmetic, peer comparison, tie-breaks, or rank.

## 6. Data Model

### `evaluation_criteria`

Stores configurable scoring criteria.

| Field | Type | Notes |
|---|---|---|
| `criterion_id` | integer primary key | Stable criterion identifier |
| `name` | text | Criterion display name |
| `description` | text | What the LLM should inspect |
| `weight` | real | Weight as percentage points |
| `max_score` | real | Maximum score for the criterion |
| `is_active` | integer or boolean | Only active criteria are used |

### `rfp_runs`

Stores one evaluation batch.

| Field | Type | Notes |
|---|---|---|
| `rfp_run_id` | text primary key | UUID or generated run identifier |
| `created_at` | text | ISO timestamp |
| `status` | text | Example: `completed`, `failed` |

### `supplier_results`

Stores the final result for each supplier in a run.

| Field | Type | Notes |
|---|---|---|
| `rfp_run_id` | text | Foreign key to `rfp_runs` |
| `supplier_name` | text | User-entered supplier name |
| `submission_date` | text | ISO date |
| `experience_rating` | real | Historical experience rating |
| `absolute_score` | real | Weighted score |
| `ppi` | real | Peer Performance Index |
| `final_rank` | integer | Rank after deterministic sort |
| `result_json` | text | Full normalized supplier result |

## 7. Pydantic Contracts

Use Pydantic models for LLM output validation and internal result normalization.

Suggested models:

```python
class CriterionResult(BaseModel):
    criterion_id: int
    score: float
    max_score: float
    justification: str
    evidence: str


class SupplierLLMResult(BaseModel):
    supplier_name: str
    criteria: list[CriterionResult]
    risks: list[str] = []
    overall_summary: str = ""


class SupplierMetadata(BaseModel):
    supplier_name: str
    submission_date: date
    experience_rating: float


class NormalizedSupplierResult(BaseModel):
    supplier: SupplierMetadata
    criteria: list[CriterionResult]
    warnings: list[str]
    absolute_score: float | None = None
    ppi: float | None = None
    final_rank: int | None = None
```

Validation should run before scoring. Scoring should use normalized results only.

## 8. Prompt Contract

Build prompts dynamically from active database criteria.

The prompt must tell the model:

- use only the supplier document as evidence
- return JSON only
- return exactly one result for every active criterion
- keep scores within each criterion's score range
- cite supporting evidence for each score
- do not calculate final weighted scores, peer benchmarks, PPI, or ranks

The LLM response should match this shape:

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

## 9. Scoring Rules

All scoring rules are deterministic.

### Absolute Weighted Score

```text
sum((criterion_score / max_score) * criterion_weight)
```

### Criterion Benchmark

```text
max(score for the same criterion across all suppliers)
```

### Criterion Gap

```text
supplier_score - benchmark_score
```

The benchmark leader has a gap of zero. Other suppliers normally have negative gaps.

### Relative Performance Percentage

```text
(supplier_score / benchmark_score) * 100
```

Safe zero rule:

- if the benchmark score is zero and the supplier score is zero, relative performance is `100`
- if the benchmark score is zero and the supplier score is greater than zero, relative performance is `100`
- the formula must never divide by zero

### Peer Performance Index

```text
sum((relative_performance_percentage / 100) * criterion_weight)
```

This gives a weighted peer-relative score on the same percentage-weight scale as the criteria.

## 10. Ranking Rules

Apply the mandatory stable sort:

1. higher PPI first
2. earlier submission date
3. higher historical experience rating
4. supplier name ascending

Then assign ranks sequentially:

```text
1, 2, 3...
```

The application should include a tie-break explanation in each supplier's stored result.

## 11. Validation Rules

The validation tool must:

- reject or warn on invalid JSON
- ensure the root object matches the expected schema
- ensure every active criterion appears exactly once
- add missing criteria with score `0`, blank evidence, and a warning
- ignore or warn about inactive or unknown criterion IDs
- clip scores below `0` to `0`
- clip scores above `max_score` to `max_score`
- normalize missing text fields to empty strings
- normalize missing `risks` to an empty list
- preserve all warnings in the final JSON

## 12. Streamlit UI Architecture

Recommended sections:

1. Criteria panel
   - active criteria
   - weights
   - maximum score
   - total active weight

2. Supplier input panel
   - multiple PDF upload
   - supplier name
   - submission date
   - historical experience rating
   - validation messages
   - Evaluate button

3. Leaderboard
   - rank
   - supplier
   - absolute score
   - PPI
   - submission date
   - experience rating

4. Detailed scorecard
   - criterion score
   - benchmark
   - gap
   - relative percentage
   - weight
   - evidence
   - justification

5. Run details
   - `RFP_RUN_ID`
   - warnings
   - tie-break explanation
   - JSON download

## 13. Error Handling

Surface failures clearly in the UI.

Expected failure cases:

- no active criteria
- active criteria weights do not total 100%
- missing supplier metadata
- unreadable PDF
- empty extracted text
- LLM returns non-JSON
- LLM omits criteria
- LLM returns out-of-range scores
- SQLite write failure

Validation and normalization warnings should not be hidden. They are part of the run details and exported JSON.

## 14. Testing Strategy

Keep tests simple and focused.

Recommended unit tests:

- active criteria load correctly
- criteria weight total is validated
- missing criterion is filled with warning
- out-of-range score is clipped with warning
- absolute weighted score is calculated correctly
- criterion benchmark and gap are calculated correctly
- relative performance handles benchmark zero
- PPI is calculated correctly
- tie-break ordering follows the required order
- complete run result can be serialized to JSON

Recommended integration checks:

- one artificial PDF can be extracted
- one supplier can be evaluated with a mocked LLM JSON response
- four suppliers can be scored and ranked from fixed validated scorecards
- one completed run is written to SQLite and read back

Do not test LLM quality with brittle assertions. Test the prompt contract, validation behavior, and deterministic formulas.

## 15. Deployment Notes

The MVP should run locally and on Streamlit Community Cloud.

Environment variables should hold API keys:

```text
OPENAI_API_KEY
```

The app should initialize or locate its SQLite database at startup. For classroom deployment, it is acceptable to seed criteria through a script before running the app.

## 16. Architecture Principles

- Keep LLM judgment separate from deterministic business rules.
- Keep prompts dynamic so criteria can change without code changes.
- Store enough detail to explain every final rank.
- Treat validation warnings as first-class output.
- Prefer small Python functions that can be tested independently.
- Do not use real confidential supplier data.
