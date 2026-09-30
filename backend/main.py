"""Hire-Me-AI backend — FastAPI server that parses a PDF resume and lets
HR chat with an AI that represents the candidate, grounded in the parsed
resume and a cached copy of the live portfolio site."""

import json
import logging
import os
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq
from pydantic import BaseModel
from pypdf import PdfReader

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODEL = "openai/gpt-oss-20b"
PORTFOLIO_URL = "https://shashank17singh.github.io"

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
RESUME_PATH = BASE_DIR / "Resume.pdf"

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class Experience(BaseModel):
    company: str | None = None
    role: str | None = None
    duration: str | None = None
    description: str | None = None
    skills_used: list[str] = []


class Resume(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    total_experience_years: float | None = None
    skills: list[str] = []
    experiences: list[Experience] = []
    education: list[str] = []
    projects: list[str] = []
    certifications: list[str] = []


RESUME_SCHEMA = Resume.model_json_schema()


class ChatRequest(BaseModel):
    question: str


# ---------------------------------------------------------------------------
# HTML → plain text extractor (for the portfolio page)
# ---------------------------------------------------------------------------
class TextExtractor(HTMLParser):
    """Strips HTML to plain text, skipping <script>/<style> blocks."""

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip = True
        elif tag == "a":
            for name, value in attrs:
                if name == "href" and value and value.startswith("http"):
                    self.text_parts.append(f"(Link: {value})")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = data.strip()
            if text:
                self.text_parts.append(text)

    def get_text(self) -> str:
        return "\n".join(self.text_parts)


# ---------------------------------------------------------------------------
# PDF reading + LLM resume parsing
# ---------------------------------------------------------------------------
def read_pdf(file_path: Path) -> str:
    """Extract text from a PDF file."""
    reader = PdfReader(file_path)
    return "\n".join(
        page_text
        for page in reader.pages
        if (page_text := page.extract_text())
    )


def parse_resume(resume_text: str) -> Resume:
    """Send resume text to the LLM and return a structured Resume object."""
    system_prompt = f"""\
You are an expert resume parser.
Extract information from the resume based on its meaning,
not only based on exact section headings.
Different resumes may use different headings.
Return ONLY valid JSON matching this schema:
{RESUME_SCHEMA}

Rules:
1. Do not invent information.
2. If a value is not available, return null.
3. If a list has no information, return an empty list.
4. Include internships inside experiences.
5. Extract skills mentioned across the entire resume."""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Parse the following resume:\n{resume_text}"},
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    return Resume(**data)


def ask_candidate(question: str, resume: Resume, portfolio_context: str) -> str:
    """Answer an HR question as the candidate, grounded in resume + portfolio."""
    system_prompt = f"""\
You are an AI assistant representing a job candidate.
Below is everything you know about the candidate from their parsed resume.
{resume.model_dump_json(indent=2)}

Below is additional context extracted directly from their live portfolio website.
{portfolio_context}

Rules:
1. Answer only using this information.
2. Never hallucinate.
3. If information is unavailable, say "I don't have enough information to answer that."
4. Be professional.
5. Answer as if HR is interviewing this candidate."""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------
_cached_resume: Resume | None = None
_cached_portfolio: str = ""


def _download_portfolio() -> str:
    """Fetch and cache the portfolio website as plain text."""
    logger.info("Downloading live portfolio from %s ...", PORTFOLIO_URL)
    resp = requests.get(
        PORTFOLIO_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15
    )
    resp.raise_for_status()
    extractor = TextExtractor()
    extractor.feed(resp.text)
    text = extractor.get_text()
    logger.info("Portfolio cached (%d chars).", len(text))
    return text


def refresh_cache() -> None:
    """Re-download the portfolio and re-parse the resume."""
    global _cached_resume, _cached_portfolio

    try:
        _cached_portfolio = _download_portfolio()
    except Exception:
        logger.exception("Failed to download portfolio")

    if RESUME_PATH.exists():
        try:
            text = read_pdf(RESUME_PATH)
            _cached_resume = parse_resume(text)
            logger.info("Resume parsed (%d chars).", len(text))
        except Exception:
            logger.exception("Failed to parse resume")
            _cached_resume = None
    else:
        logger.warning("Resume file not found at %s", RESUME_PATH)
        _cached_resume = None


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        refresh_cache()
    except Exception:
        logger.exception("Error during startup cache refresh")
    yield


app = FastAPI(
    title="Hire-Me-AI",
    description="Chat with an AI that represents a job candidate.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def home():
    """Serve the interactive chat UI."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/chat")
def chat(request: ChatRequest):
    """Ask a question about the candidate."""
    global _cached_resume
    if not _cached_resume:
        refresh_cache()
    if not _cached_resume:
        return {
            "answer": (
                "Sorry, I couldn't access my resume and portfolio context "
                "right now. Please try again in a few minutes!"
            )
        }
    answer = ask_candidate(request.question, _cached_resume, _cached_portfolio)
    return {"answer": answer}


@app.post("/refresh")
def refresh():
    """Force a cache refresh of resume and portfolio data."""
    refresh_cache()
    if not _cached_resume:
        raise HTTPException(
            status_code=500,
            detail="Failed to parse or download resume.",
        )
    return {"status": "success", "message": "Cache refreshed successfully."}
