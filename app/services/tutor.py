"""Shared tutoring steps used by both the text API and Buddy firmware."""
import re
from datetime import datetime, timezone

from database import supabase

AI_MODEL = "deepseek-chat"


def extract_strand(answer_text: str) -> str | None:
    sub_strand_match = re.search(r"[Ss]ub-?strand\s+([A-Za-z]{0,3}\.?\d+(?:\.\d+)?)", answer_text)
    if sub_strand_match:
        return sub_strand_match.group(1)

    mada_match = re.search(r"[Mm][Aa][Dd][Aa]\s+(\d+\.\d+)", answer_text)
    if mada_match:
        return mada_match.group(1)

    dash_pattern = re.search(r"[—-]\s*([A-Za-z]{0,3}\.?\d+\.\d+)\s+[A-Z]", answer_text)
    if dash_pattern:
        return dash_pattern.group(1)

    code_title_pattern = re.search(r"\b([A-Za-z]{1,3}\.\d+(?:\.\d+)?)\s+[A-Z][a-z]", answer_text)
    if code_title_pattern:
        return code_title_pattern.group(1)

    all_numbers = re.findall(r"\b([A-Za-z]{0,3}\.?\d+\.\d+)\b", answer_text)
    for num in all_numbers:
        if not num.endswith(".0"):
            return num

    strand_match = re.search(r"([Ss]trand|[Mm]ada)\s+(\d+\.\d+)", answer_text)
    if strand_match:
        return strand_match.group(2)

    return None


def log_interaction(
    *,
    device: dict,
    student_id: str | None,
    question: str,
    language: str,
    subject: str,
    grade: int,
    strand_id: str | None,
    answer: str,
    latency_ms: int,
) -> None:
    """Records an interaction and marks the device as seen. Never raises:
    a logging failure must not cost the student their answer."""
    try:
        supabase.table("interactions").insert({
            "device_id": device["id"],
            "student_id": student_id,
            "org_id": device["org_id"],
            "transcript": question,
            "language_detected": language,
            "interaction_type": "qa",
            "subject": subject,
            "grade": grade,
            "strand_id": strand_id,
            "response_text": answer,
            "response_latency_ms": latency_ms,
            "ai_model": AI_MODEL,
        }).execute()

        supabase.table("devices").update(
            {"last_seen_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", device["id"]).execute()

    except Exception as e:
        print(f"WARNING: Failed to log interaction: {e}")
