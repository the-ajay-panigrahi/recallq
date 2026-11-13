from typing import Annotated
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from backend import core

app = FastAPI(title="RecallQ API", version="0.1.0")


@app.exception_handler(core.ServiceError)
async def service_error(request: Request, exc: core.ServiceError):
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "INVALID_INPUT",
                "message": "Check the query and selected sources.",
            }
        },
    )


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=20, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def trim_query(cls, value):
        if not value.strip():
            raise ValueError("Empty query")
        return value.strip()


class SummaryRequest(SearchRequest):
    corpus_revision: str = Field(max_length=100)
    source_ids: list[UUID] = Field(min_length=1, max_length=6)


def require_key(key):
    if not key or not key.strip() or len(key) > 512:
        raise core.ServiceError(
            401, "KEY_REQUIRED", "Enter your Gemini API key to continue."
        )
    return key.strip()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/corpus")
def corpus():
    m = core.manifest()
    return {
        **m,
        "indexed_videos": sum(v["status"] == "indexed" for v in m["videos"]),
        "total_videos": len(m["videos"]),
        "index_connected": bool(core.config("QDRANT_URL")),
    }


@app.post("/api/search")
async def search(
    body: SearchRequest, x_gemini_api_key: Annotated[str | None, Header()] = None
):
    return await core.search(body.query, body.limit, require_key(x_gemini_api_key))


@app.post("/api/summarize")
async def summarize(
    body: SummaryRequest, x_gemini_api_key: Annotated[str | None, Header()] = None
):
    return await core.summarize(
        body.query,
        body.corpus_revision,
        list(dict.fromkeys(str(i) for i in body.source_ids)),
        require_key(x_gemini_api_key),
    )


build = Path(__file__).resolve().parents[1] / "dist"
if build.is_dir():
    app.mount("/", StaticFiles(directory=build, html=True), name="frontend")
