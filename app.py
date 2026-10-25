import io
import json
import os
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError
from pypdf import PdfReader


APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "rfp_evaluation.db"

DEFAULT_CRITERIA = [
    (
        1,
        "Technical Capability",
        "Architecture, integrations, scalability, technical fit",
        30.0,
        10.0,
        1,
    ),
    (
        2,
        "Implementation Plan",
        "Timeline, milestones, staffing, risk plan",
        20.0,
        10.0,
        1,
    ),
    (
        3,
        "Commercial Value",
        "Pricing clarity, total cost, assumptions",
        20.0,
        10.0,
        1,
    ),
    (
        4,
        "Security & Compliance",
        "Controls, certifications, privacy, auditability",
        20.0,
        10.0,
        1,
    ),
    (
        5,
        "Support & Experience",
        "Support model, similar projects, references",
        10.0,
        10.0,
        1,
    ),
]


class Criterion(BaseModel):
    criterion_id: int
    name: str
    description: str
    weight: float
    max_score: float
    is_active: bool = True


class CriterionResult(BaseModel):
    criterion_id: int
    score: float = Field(default=0)
    max_score: float = Field(default=10)
    justification: str = ""
    evidence: str = ""


class SupplierLLMResult(BaseModel):
    supplier_name: str = ""
    criteria: list[CriterionResult] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    overall_summary: str = ""


class SupplierInput(BaseModel):
    supplier_name: str
    submission_date: date
    experience_rating: float
    file_name: str


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evaluation_criteria (
                criterion_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                weight REAL NOT NULL,
                max_score REAL NOT NULL,
                is_active INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rfp_runs (
                rfp_run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS supplier_results (
                rfp_run_id TEXT NOT NULL,
                supplier_name TEXT NOT NULL,
                submission_date TEXT NOT NULL,
                experience_rating REAL NOT NULL,
                absolute_score REAL NOT NULL,
                ppi REAL NOT NULL,
                final_rank INTEGER NOT NULL,
                result_json TEXT NOT NULL,
                FOREIGN KEY (rfp_run_id) REFERENCES rfp_runs (rfp_run_id)
            )
            """
        )
        existing = conn.execute("SELECT COUNT(*) FROM evaluation_criteria").fetchone()[0]
        if existing == 0:
            conn.executemany(
                """
                INSERT INTO evaluation_criteria (
                    criterion_id, name, description, weight, max_score, is_active
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                DEFAULT_CRITERIA,
            )
        conn.commit()


def load_active_criteria() -> list[Criterion]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT criterion_id, name, description, weight, max_score, is_active
            FROM evaluation_criteria
            WHERE is_active = 1
            ORDER BY criterion_id
            """
        ).fetchall()
    return [Criterion(**dict(row)) for row in rows]


def save_run(rfp_run_id: str, results: list[dict[str, Any]]) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO rfp_runs (rfp_run_id, created_at, status) VALUES (?, ?, ?)",
            (rfp_run_id, created_at, "completed"),
        )
        conn.executemany(
            """
            INSERT INTO supplier_results (
                rfp_run_id, supplier_name, submission_date, experience_rating,
                absolute_score, ppi, final_rank, result_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    rfp_run_id,
                    result["supplier"]["supplier_name"],
                    result["supplier"]["submission_date"],
                    result["supplier"]["experience_rating"],
                    result["absolute_score"],
                    result["ppi"],
                    result["final_rank"],
                    json.dumps(result, indent=2),
                )
                for result in results
            ],
        )
        conn.commit()


def get_secret(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value:
        return value
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def extract_pdf_text(uploaded_file: Any) -> str:
    reader = PdfReader(io.BytesIO(uploaded_file.getvalue()))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages).strip()


def criteria_prompt_block(criteria: list[Criterion]) -> str:
    return "\n".join(
        (
            f"- criterion_id={item.criterion_id}; name={item.name}; "
            f"max_score={item.max_score}; description={item.description}"
        )
        for item in criteria
    )


def build_prompt(
    supplier: SupplierInput,
    criteria: list[Criterion],
    document_text: str,
) -> str:
    return f"""
You evaluate one supplier RFP proposal.

Return JSON only. Do not wrap it in markdown.
Use only evidence present in the supplier document.
Return exactly one criteria item for every active criterion.
Keep every score between 0 and the criterion max_score.
Do not calculate weighted totals, peer benchmarks, PPI, tie-breaks, or ranks.

Supplier metadata:
- supplier_name: {supplier.supplier_name}
- submission_date: {supplier.submission_date.isoformat()}
- historical_experience_rating: {supplier.experience_rating}

Active criteria:
{criteria_prompt_block(criteria)}

Required JSON shape:
{{
  "supplier_name": "{supplier.supplier_name}",
  "criteria": [
    {{
      "criterion_id": 1,
      "score": 8,
      "max_score": 10,
      "justification": "short reason",
      "evidence": "short quote or paraphrase from the proposal"
    }}
  ],
  "risks": ["risk text"],
  "overall_summary": "summary text"
}}

Supplier document:
{document_text[:20000]}
""".strip()


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def evaluate_with_llm(
    supplier: SupplierInput,
    criteria: list[Criterion],
    document_text: str,
    model: str,
    api_key: str,
) -> tuple[dict[str, Any], str]:
    client = OpenAI(api_key=api_key)
    prompt = build_prompt(supplier, criteria, document_text)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an evidence-grounded RFP evaluation assistant. "
                    "You return strict JSON and never calculate final rankings."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )
    raw_text = response.choices[0].message.content or "{}"
    return extract_json_object(raw_text), raw_text


def demo_llm_result(
    supplier: SupplierInput,
    criteria: list[Criterion],
    document_text: str,
) -> tuple[dict[str, Any], str]:
    lower_text = document_text.lower()
    results = []
    risks = []
    for criterion in criteria:
        score = 6.0
        evidence = "No strong direct evidence found; demo mode estimated a neutral score."
        if any(word in lower_text for word in ["strong", "robust", "certified", "proven"]):
            score += 1.0
        if any(word in lower_text for word in ["weak", "limited", "vague", "missing"]):
            score -= 1.0
        if criterion.criterion_id == 3 and any(
            word in lower_text for word in ["lowest price", "low price", "cost saving"]
        ):
            score += 1.0
        if criterion.criterion_id == 4 and any(
            word in lower_text for word in ["security", "compliance", "audit", "privacy"]
        ):
            score += 1.0
            evidence = "Document references security, compliance, audit, or privacy."
        if criterion.criterion_id == 5 and any(
            word in lower_text for word in ["support", "references", "experience"]
        ):
            score += 1.0
            evidence = "Document references support, references, or experience."
        score = max(0.0, min(score, criterion.max_score))
        results.append(
            {
                "criterion_id": criterion.criterion_id,
                "score": score,
                "max_score": criterion.max_score,
                "justification": "Demo mode score based on simple keyword signals.",
                "evidence": evidence,
            }
        )
    risks.append("Demo mode was used, so scores are not true LLM judgments.")
    payload = {
        "supplier_name": supplier.supplier_name,
        "criteria": results,
        "risks": risks,
        "overall_summary": "Demo evaluation generated without an OpenAI API key.",
    }
    return payload, json.dumps(payload, indent=2)


def normalize_llm_result(
    payload: dict[str, Any],
    supplier: SupplierInput,
    criteria: list[Criterion],
) -> dict[str, Any]:
    warnings = []
    try:
        parsed = SupplierLLMResult.model_validate(payload)
    except ValidationError as exc:
        parsed = SupplierLLMResult(supplier_name=supplier.supplier_name, criteria=[])
        warnings.append(f"LLM response schema was invalid and was normalized: {exc.errors()[0]['msg']}")

    criteria_by_id = {item.criterion_id: item for item in criteria}
    seen: dict[int, CriterionResult] = {}

    for item in parsed.criteria:
        if item.criterion_id not in criteria_by_id:
            warnings.append(f"Unknown criterion_id {item.criterion_id} was ignored.")
            continue
        if item.criterion_id in seen:
            warnings.append(f"Duplicate criterion_id {item.criterion_id} was ignored.")
            continue

        criterion = criteria_by_id[item.criterion_id]
        score = item.score
        if score < 0:
            warnings.append(f"{criterion.name} score was below 0 and clipped to 0.")
            score = 0.0
        if score > criterion.max_score:
            warnings.append(
                f"{criterion.name} score exceeded {criterion.max_score} and was clipped."
            )
            score = criterion.max_score

        seen[item.criterion_id] = CriterionResult(
            criterion_id=item.criterion_id,
            score=score,
            max_score=criterion.max_score,
            justification=item.justification or "",
            evidence=item.evidence or "",
        )

    normalized_criteria = []
    for criterion in criteria:
        item = seen.get(criterion.criterion_id)
        if item is None:
            warnings.append(f"{criterion.name} was missing and received score 0.")
            item = CriterionResult(
                criterion_id=criterion.criterion_id,
                score=0.0,
                max_score=criterion.max_score,
                justification="Missing from LLM response.",
                evidence="",
            )
        normalized_criteria.append(item.model_dump())

    return {
        "supplier": supplier.model_dump(mode="json"),
        "llm_supplier_name": parsed.supplier_name or supplier.supplier_name,
        "criteria": normalized_criteria,
        "risks": parsed.risks if isinstance(parsed.risks, list) else [],
        "overall_summary": parsed.overall_summary or "",
        "warnings": warnings,
    }


def add_scores(results: list[dict[str, Any]], criteria: list[Criterion]) -> list[dict[str, Any]]:
    criteria_by_id = {item.criterion_id: item for item in criteria}

    for result in results:
        absolute_score = 0.0
        for item in result["criteria"]:
            criterion = criteria_by_id[item["criterion_id"]]
            max_score = criterion.max_score or 1.0
            absolute_score += (item["score"] / max_score) * criterion.weight
        result["absolute_score"] = round(absolute_score, 4)

    benchmarks = {}
    for criterion in criteria:
        benchmarks[criterion.criterion_id] = max(
            result_item["score"]
            for result in results
            for result_item in result["criteria"]
            if result_item["criterion_id"] == criterion.criterion_id
        )

    for result in results:
        ppi = 0.0
        for item in result["criteria"]:
            criterion = criteria_by_id[item["criterion_id"]]
            benchmark = benchmarks[item["criterion_id"]]
            if benchmark == 0:
                relative = 100.0
            else:
                relative = (item["score"] / benchmark) * 100
            gap = item["score"] - benchmark
            item["benchmark_score"] = round(benchmark, 4)
            item["criterion_gap"] = round(gap, 4)
            item["relative_performance_percent"] = round(relative, 4)
            item["weight"] = criterion.weight
            item["criterion_name"] = criterion.name
            ppi += (relative / 100) * criterion.weight
        result["ppi"] = round(ppi, 4)

    ranked = sorted(
        results,
        key=lambda item: (
            -item["ppi"],
            item["supplier"]["submission_date"],
            -float(item["supplier"]["experience_rating"]),
            item["supplier"]["supplier_name"].lower(),
        ),
    )
    for index, result in enumerate(ranked, start=1):
        result["final_rank"] = index
        result["tie_break_explanation"] = (
            "Sorted by higher PPI, then earlier submission date, then higher "
            "experience rating, then supplier name ascending."
        )
    return ranked


def build_leaderboard(results: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for result in results:
        rows.append(
            {
                "Rank": result["final_rank"],
                "Supplier": result["supplier"]["supplier_name"],
                "Absolute Score": result["absolute_score"],
                "PPI": result["ppi"],
                "Submission Date": result["supplier"]["submission_date"],
                "Experience Rating": result["supplier"]["experience_rating"],
            }
        )
    return pd.DataFrame(rows)


def validate_ready(
    suppliers: list[SupplierInput],
    uploaded_files: list[Any],
    criteria: list[Criterion],
    api_key: str,
    use_demo_mode: bool,
) -> list[str]:
    errors = []
    if not criteria:
        errors.append("No active criteria found.")
    if criteria and abs(sum(item.weight for item in criteria) - 100.0) > 0.01:
        errors.append("Active criteria weights must total 100%.")
    if not uploaded_files:
        errors.append("Upload at least one supplier PDF.")
    if len(suppliers) != len(uploaded_files):
        errors.append("Every uploaded PDF needs supplier metadata.")
    for supplier in suppliers:
        if not supplier.supplier_name.strip():
            errors.append(f"{supplier.file_name}: supplier name is required.")
        if not 0 <= supplier.experience_rating <= 10:
            errors.append(f"{supplier.file_name}: experience rating must be between 0 and 10.")
    if not use_demo_mode and not api_key:
        errors.append("Set OPENAI_API_KEY in Streamlit secrets or enable demo mode.")
    return errors


def render_criteria(criteria: list[Criterion]) -> None:
    st.subheader("Criteria")
    df = pd.DataFrame(
        [
            {
                "ID": item.criterion_id,
                "Criterion": item.name,
                "Weight": item.weight,
                "Max Score": item.max_score,
                "Description": item.description,
            }
            for item in criteria
        ]
    )
    st.dataframe(df, use_container_width=True, hide_index=True)
    total_weight = sum(item.weight for item in criteria)
    st.caption(f"Active weight total: {total_weight:.2f}%")
    if abs(total_weight - 100.0) > 0.01:
        st.error("Active criteria weights should total 100%.")


def render_supplier_inputs(uploaded_files: list[Any]) -> list[SupplierInput]:
    suppliers = []
    if not uploaded_files:
        st.info("Upload supplier PDF files to enter metadata.")
        return suppliers

    st.subheader("Supplier Input")
    if len(uploaded_files) < 4:
        st.warning("The assignment expects at least four artificial supplier PDFs.")

    for index, uploaded_file in enumerate(uploaded_files):
        default_name = Path(uploaded_file.name).stem.replace("_", " ").replace("-", " ").title()
        with st.expander(uploaded_file.name, expanded=True):
            col1, col2, col3 = st.columns([2, 1, 1])
            supplier_name = col1.text_input(
                "Supplier name",
                value=default_name,
                key=f"name_{index}_{uploaded_file.name}",
            )
            submission_date = col2.date_input(
                "Submission date",
                value=date.today(),
                key=f"date_{index}_{uploaded_file.name}",
            )
            experience_rating = col3.number_input(
                "Experience rating",
                min_value=0.0,
                max_value=10.0,
                value=5.0,
                step=0.5,
                key=f"rating_{index}_{uploaded_file.name}",
            )
            suppliers.append(
                SupplierInput(
                    supplier_name=supplier_name,
                    submission_date=submission_date,
                    experience_rating=experience_rating,
                    file_name=uploaded_file.name,
                )
            )
    return suppliers


def render_results(rfp_run_id: str, results: list[dict[str, Any]]) -> None:
    st.subheader("Leaderboard")
    st.dataframe(build_leaderboard(results), use_container_width=True, hide_index=True)

    st.subheader("Detailed Scorecards")
    supplier_names = [result["supplier"]["supplier_name"] for result in results]
    selected_name = st.selectbox("Supplier", supplier_names)
    selected = next(
        result for result in results if result["supplier"]["supplier_name"] == selected_name
    )
    st.write(selected["overall_summary"])
    scorecard_df = pd.DataFrame(
        [
            {
                "Criterion": item["criterion_name"],
                "Score": item["score"],
                "Benchmark": item["benchmark_score"],
                "Gap": item["criterion_gap"],
                "Relative %": item["relative_performance_percent"],
                "Weight": item["weight"],
                "Evidence": item["evidence"],
                "Justification": item["justification"],
            }
            for item in selected["criteria"]
        ]
    )
    st.dataframe(scorecard_df, use_container_width=True, hide_index=True)

    if selected["risks"]:
        st.markdown("**Risks**")
        for risk in selected["risks"]:
            st.write(f"- {risk}")

    st.subheader("Run Details")
    st.code(rfp_run_id)
    all_warnings = [
        f"{result['supplier']['supplier_name']}: {warning}"
        for result in results
        for warning in result["warnings"]
    ]
    if all_warnings:
        st.warning("\n".join(all_warnings))
    else:
        st.success("No validation warnings.")
    st.caption(selected["tie_break_explanation"])

    export_payload = {"rfp_run_id": rfp_run_id, "results": results}
    st.download_button(
        "Download complete result JSON",
        data=json.dumps(export_payload, indent=2),
        file_name=f"{rfp_run_id}.json",
        mime="application/json",
    )


def run_evaluation(
    suppliers: list[SupplierInput],
    uploaded_files: list[Any],
    criteria: list[Criterion],
    api_key: str,
    model: str,
    use_demo_mode: bool,
) -> tuple[str, list[dict[str, Any]]]:
    normalized_results = []
    progress = st.progress(0)
    status = st.empty()

    for index, (supplier, uploaded_file) in enumerate(zip(suppliers, uploaded_files), start=1):
        status.write(f"Evaluating {supplier.supplier_name}...")
        document_text = extract_pdf_text(uploaded_file)
        if not document_text:
            raise ValueError(f"{uploaded_file.name} did not contain extractable text.")

        if use_demo_mode:
            payload, raw_text = demo_llm_result(supplier, criteria, document_text)
        else:
            payload, raw_text = evaluate_with_llm(
                supplier=supplier,
                criteria=criteria,
                document_text=document_text,
                model=model,
                api_key=api_key,
            )

        normalized = normalize_llm_result(payload, supplier, criteria)
        normalized["raw_llm_output"] = raw_text
        normalized_results.append(normalized)
        progress.progress(index / len(uploaded_files))

    ranked_results = add_scores(normalized_results, criteria)
    rfp_run_id = f"RFP-{uuid.uuid4().hex[:10].upper()}"
    save_run(rfp_run_id, ranked_results)
    status.write("Evaluation complete.")
    return rfp_run_id, ranked_results


def main() -> None:
    st.set_page_config(page_title="Agentic RFP Evaluation", layout="wide")
    init_db()

    st.title("Agentic RFP Evaluation and Supplier Ranking")
    st.caption(
        "Upload supplier proposal PDFs, evaluate them against active criteria, "
        "and generate an explainable deterministic leaderboard."
    )

    criteria = load_active_criteria()
    render_criteria(criteria)

    with st.sidebar:
        st.header("LLM Settings")
        model = st.text_input("OpenAI model", value=get_secret("OPENAI_MODEL", "gpt-4o-mini"))
        api_key = get_secret("OPENAI_API_KEY")
        use_demo_mode = st.checkbox(
            "Demo mode without OpenAI",
            value=not bool(api_key),
            help="Use only for UI testing. Real submission should use an OpenAI API key.",
        )
        if api_key and not use_demo_mode:
            st.success("OPENAI_API_KEY is configured.")
        elif use_demo_mode:
            st.info("Demo mode uses simple keyword scoring, not real LLM judgment.")
        else:
            st.warning("OPENAI_API_KEY is not configured.")

    uploaded_files = st.file_uploader(
        "Upload supplier RFP PDFs",
        type=["pdf"],
        accept_multiple_files=True,
    )
    suppliers = render_supplier_inputs(uploaded_files)

    errors = validate_ready(suppliers, uploaded_files, criteria, api_key, use_demo_mode)
    if errors:
        for error in errors:
            st.error(error)

    evaluate = st.button("Evaluate suppliers", type="primary", disabled=bool(errors))
    if evaluate:
        try:
            rfp_run_id, results = run_evaluation(
                suppliers=suppliers,
                uploaded_files=uploaded_files,
                criteria=criteria,
                api_key=api_key,
                model=model,
                use_demo_mode=use_demo_mode,
            )
            st.session_state["rfp_run_id"] = rfp_run_id
            st.session_state["results"] = results
        except Exception as exc:
            st.error(f"Evaluation failed: {exc}")

    if "results" in st.session_state and "rfp_run_id" in st.session_state:
        render_results(st.session_state["rfp_run_id"], st.session_state["results"])


if __name__ == "__main__":
    main()
