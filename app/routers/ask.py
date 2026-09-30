from fastapi import APIRouter
from pydantic import BaseModel
from datetime import datetime, timezone
import re
from services.ai import ask_ai, _science_strands, _kiswahili_strands, _ai_literacy_strands
from services.content_loader import find_diagram_for_strand, detect_language
from database import supabase

router = APIRouter()

_SUBJECT_STRANDS = {
    "science": _science_strands,
    "kiswahili": _kiswahili_strands,
    "ai_literacy": _ai_literacy_strands,
}

class AskRequest(BaseModel):
    question: str
    device_id: str
    student_id: str | None = None
    grade: int = 7
    subject: str = "science"

class DiagramReference(BaseModel):
    topic: str
    url: str
    license: str

class AskResponse(BaseModel):
    answer: str
    strand_detected: str | None = None
    diagrams: list[DiagramReference] = []

def extract_strand(answer_text: str) -> str | None:
    # Pattern 1: explicit "Sub-strand X.X" or "Sub-strand AI.X"
    sub_strand_match = re.search(r"[Ss]ub-?strand\s+([A-Za-z]{0,3}\.?\d+(?:\.\d+)?)", answer_text)
    if sub_strand_match:
        return sub_strand_match.group(1)

    # Pattern 2: "Mada X.X" or "MADA X.X" (any capitalisation)
    mada_match = re.search(r"[Mm][Aa][Dd][Aa]\s+(\d+\.\d+)", answer_text)
    if mada_match:
        return mada_match.group(1)

    # Pattern 3: dash-separated sub-strand, e.g. "— 1.4 Laboratory..."
    dash_pattern = re.search(r"[—-]\s*([A-Za-z]{0,3}\.?\d+\.\d+)\s+[A-Z]", answer_text)
    if dash_pattern:
        return dash_pattern.group(1)

    # Pattern 4: code directly followed by a title, e.g. "AI.2 Checking If..."
    code_title_pattern = re.search(r"\b([A-Za-z]{1,3}\.\d+(?:\.\d+)?)\s+[A-Z][a-z]", answer_text)
    if code_title_pattern:
        return code_title_pattern.group(1)

    # Pattern 5: any strand-looking number not ending in .0
    all_numbers = re.findall(r"\b([A-Za-z]{0,3}\.?\d+\.\d+)\b", answer_text)
    for num in all_numbers:
        if not num.endswith(".0"):
            return num

    # Fallback: "Strand X.0" or "Mada X.0" (broad strand-level only)
    strand_match = re.search(r"([Ss]trand|[Mm]ada)\s+(\d+\.\d+)", answer_text)
    if strand_match:
        return strand_match.group(2)

    return None

@router.post("/ask", response_model=AskResponse)
def ask(request: AskRequest):
    start_time = datetime.now(timezone.utc)

    answer = ask_ai(request.question, subject=request.subject)
    strand_id = extract_strand(answer)
    detected_lang = detect_language(request.question)

    diagrams = []
    if strand_id:
        strands_for_subject = _SUBJECT_STRANDS.get(request.subject, _science_strands)
        matched_diagrams = find_diagram_for_strand(strands_for_subject, strand_id)
        diagrams = [
            DiagramReference(topic=d["topic"], url=d["url"], license=d["license"])
            for d in matched_diagrams
        ]

    end_time = datetime.now(timezone.utc)
    latency_ms = int((end_time - start_time).total_seconds() * 1000)

    try:
        device_lookup = supabase.table("devices") \
            .select("id, org_id") \
            .eq("device_id", request.device_id) \
            .execute()

        device_row = device_lookup.data[0] if device_lookup.data else None
        device_uuid = device_row["id"] if device_row else None
        org_uuid = device_row["org_id"] if device_row else None

        supabase.table("interactions").insert({
            "device_id": device_uuid,
            "student_id": request.student_id,
            "org_id": org_uuid,
            "transcript": request.question,
            "language_detected": detected_lang,
            "interaction_type": "qa",
            "subject": request.subject,
            "grade": request.grade,
            "strand_id": strand_id,
            "response_text": answer,
            "response_latency_ms": latency_ms,
            "ai_model": "deepseek-chat",
        }).execute()

    except Exception as e:
        print(f"WARNING: Failed to log interaction: {e}")

    return AskResponse(answer=answer, strand_detected=strand_id, diagrams=diagrams)