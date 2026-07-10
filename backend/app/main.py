from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import create_db_and_tables
from app.api.main import api_router


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


app = FastAPI(
    title="Rayda Fleet Copilot API",
    description="Agentic reasoning engine for IT asset telemetry and stateful actions.",
    version="1.0.0",
    openapi_url="/openapi.json",
    generate_unique_id=custom_generate_unique_id,
)

origins = [
    "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()


app.include_router(api_router, prefix="/api/v1")    


@app.get("/health", tags=["System"])
def health_check():
    """Simple health check endpoint."""
    return {"status": "healthy", "service": "fleet-copilot-agent"}