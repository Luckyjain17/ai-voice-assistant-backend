import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from app.database import engine, Base
from sqlalchemy import text
from app.models import CallRecord
from app.routes.calls import router as call_router

load_dotenv()

app = FastAPI()

# Base.metadata.create_all(bind=engine)

# Ensure recent optional columns exist in the database (safe, idempotent)
try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE IF EXISTS call_records ADD COLUMN IF NOT EXISTS external_id VARCHAR;"))
except Exception:
    # best-effort; ignore failures here so startup doesn't crash
    pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("BACKEND_CORS_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(call_router)

@app.get("/")
def home():
    return {"message": "Backend Running"}
