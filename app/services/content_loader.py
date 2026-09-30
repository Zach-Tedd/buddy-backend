import json
import os

BASE_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "..", "content")

def load_all_strands(subject="science"):
    """Loads all strand/theme JSON files for a given subject folder
    and returns them as a list."""
    subject_dir = os.path.join(BASE_CONTENT_DIR, subject)
    strands = []
    if not os.path.isdir(subject_dir):
        return strands
    for filename in sorted(os.listdir(subject_dir)):
        if filename.endswith(".json"):
            filepath = os.path.join(subject_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                strands.append(data["strand"])
    return strands

def build_curriculum_summary(strands):
    """Builds a compact text summary of the full curriculum structure
    for injection into the AI system prompt."""
    lines = []
    for strand in strands:
        lines.append(f"STRAND/MADA {strand['id']}: {strand['title']}")
        for sub in strand["sub_strands"]:
            lines.append(f"  {sub['id']} {sub['title']}")
            for concept in sub.get("key_concepts", [])[:3]:
                lines.append(f"    - {concept}")
    return "\n".join(lines)

def get_sample_qa_pairs(strands, limit=6):
    """Pulls a handful of real Q&A examples across strands to show the
    AI the expected tone, depth, and Kenyan-context style."""
    samples = []
    for strand in strands:
        for sub in strand["sub_strands"]:
            for qa in sub.get("qa_pairs", [])[:1]:
                samples.append(qa)
                if len(samples) >= limit:
                    return samples
    return samples

def find_diagram_for_strand(strands, strand_id: str) -> list:
    """Given a sub-strand ID, returns any confirmed diagrams attached
    to that sub-strand."""
    for strand in strands:
        for sub in strand["sub_strands"]:
            if sub["id"] == strand_id:
                diagrams = sub.get("diagrams", [])
                return [d for d in diagrams if d.get("status") == "confirmed"]
    return []

def get_pronunciation_pairs(strands) -> list:
    """Collects all KICD-specified phoneme pairs flagged across a
    subject's sub-strands, for the pronunciation feature."""
    pairs = []
    for strand in strands:
        for sub in strand["sub_strands"]:
            focus = sub.get("pronunciation_focus")
            if focus:
                pairs.append({
                    "sub_strand_id": sub["id"],
                    "sub_strand_title": sub["title"],
                    **focus
                })
    return pairs
_SWAHILI_MARKERS = {
    "nini", "vipi", "kwa", "gani", "vipi?", "je", "ni", "na", "wa", "ya",
    "kwenye", "kutoka", "hii", "hiyo", "huyu", "hawa", "kuwa", "kuhusu",
    "unaweza", "eleza", "elezea", "tofauti", "kati", "kwa nini", "namna"
}

def detect_language(text: str) -> str:
    """Lightweight, free heuristic to flag whether a question is likely
    Kiswahili or English, for logging purposes. Not perfect on short or
    mixed sentences — the AI itself handles actual response language
    based on its own reading of the question."""
    words = text.lower().replace("?", "").replace(",", "").split()
    if not words:
        return "en"
    swahili_hits = sum(1 for w in words if w in _SWAHILI_MARKERS)
    ratio = swahili_hits / len(words)
    if ratio >= 0.25:
        return "sw"
    elif swahili_hits > 0:
        return "en-sw"
    return "en"