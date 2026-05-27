import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db
from app.env import load_backend_env
from app.routes.calls import router as call_router

load_backend_env()

app = FastAPI()

app.add_middleware(
CORSMiddleware,
allow_origins=["*"],
allow_credentials=True,
allow_methods=["*"],
allow_headers=["*"],
)


app.include_router(call_router)


@app.on_event("startup")
def startup() -> None:
    init_db()

@app.get("/")
def home():
    return {"message": "Backend Running"}
