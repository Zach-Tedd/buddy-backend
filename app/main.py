from fastapi import FastAPI
from database import supabase
from routers import ask, students, firmware

app = FastAPI(
    title="Buddy API",
    description="Backend API for Buddy — AI Learning Companion for CBC Classrooms",
    version="1.0.0"
)

app.include_router(ask.router, prefix="/api/v1", tags=["ask"])
app.include_router(students.router, prefix="/api/v1", tags=["students"])
# Buddy firmware (UNIHIKER K10 voice devices): /buddy/ota/ and /buddy/ws
app.include_router(firmware.router)

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Buddy API",
        "version": "1.0.0"
    }

@app.get("/health")
def health_check():
    try:
        response = supabase.table("organisations").select("id").limit(1).execute()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "api": "healthy",
        "database": db_status
    }