from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from database import supabase

router = APIRouter()

class StudentCreate(BaseModel):
    org_id: str
    first_name: str
    last_name: str
    grade: int
    class_section: str | None = None
    preferred_language: str = "en"
    pin: str | None = None

class StudentResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    grade: int

@router.post("/students", response_model=StudentResponse)
def create_student(student: StudentCreate):
    if student.grade not in (7, 8, 9):
        raise HTTPException(status_code=400, detail="Grade must be 7, 8, or 9")

    result = supabase.table("users").insert({
        "org_id": student.org_id,
        "role": "student",
        "first_name": student.first_name,
        "last_name": student.last_name,
        "grade": student.grade,
        "class_section": student.class_section,
        "preferred_language": student.preferred_language,
        "pin": student.pin,
    }).execute()

    row = result.data[0]
    return StudentResponse(
        id=row["id"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        grade=row["grade"]
    )

@router.get("/students")
def list_students(org_id: str):
    result = supabase.table("users") \
        .select("id, first_name, last_name, grade, class_section, pin") \
        .eq("org_id", org_id) \
        .eq("role", "student") \
        .execute()
    return result.data