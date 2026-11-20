from fastapi import APIRouter, Depends
from pydantic import BaseModel
from datetime import datetime, timezone
from auth import require_device
from services.ai import ask_ai, SUBJECT_STRANDS, _science_strands
from services.content_loader import find_diagram_for_strand, detect_language
from services.tutor import extract_strand, log_interaction

router = APIRouter()

class AskRequest(BaseModel):
    question: str
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

@router.post("/ask", response_model=AskResponse)
def ask(request: AskRequest, device: dict = Depends(require_device)):
    start_time = datetime.now(timezone.utc)

    answer = ask_ai(request.question, subject=request.subject)
    strand_id = extract_strand(answer)
    detected_lang = detect_language(request.question)

    diagrams = []
    if strand_id:
        strands_for_subject = SUBJECT_STRANDS.get(request.subject, _science_strands)
        matched = find_diagram_for_strand(strands_for_subject, strand_id)
        diagrams = [
            DiagramReference(topic=d["topic"], url=d["url"], license=d["license"])
            for d in matched
        ]

    latency_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

    log_interaction(
        device=device,
        student_id=request.student_id,
        question=request.question,
        language=detected_lang,
        subject=request.subject,
        grade=request.grade,
        strand_id=strand_id,
        answer=answer,
        latency_ms=latency_ms,
    )

    return AskResponse(answer=answer, strand_detected=strand_id, diagrams=diagrams)
