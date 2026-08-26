import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from groq import Groq
from pydantic import BaseModel
from pypdf import PdfReader

load_dotenv()

client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)

model = "mixtral-8x7b-32768"


from contextlib import asynccontextmanager
import requests
from html.parser import HTMLParser

cached_resume = None
cached_portfolio = ""

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
RESUME_PATH = BASE_DIR / "my_resume.pdf"

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self._skip = False
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip = True
    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip = False
    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self.text_parts.append(text)
    def get_text(self):
        return "\n".join(self.text_parts)

def download_portfolio():
    global cached_portfolio
    url = "https://shashank17singh.github.io"
    print("Downloading live portfolio...")
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        resp.raise_for_status()
        extractor = TextExtractor()
        extractor.feed(resp.text)
        cached_portfolio = extractor.get_text()
        print(f"Portfolio downloaded and cached ({len(cached_portfolio)} chars).")
    except Exception as e:
        print("Failed to download portfolio:", e)

def fetch_resume_from_url(url: str) -> str:
    """Fetch resume text from any publicly accessible URL (Google Docs, Notion, GitHub raw, etc.)"""
    resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
    resp.raise_for_status()
    content_type = resp.headers.get('Content-Type', '')
    
    if 'text/html' in content_type:
        # Google Docs published page, Notion, portfolio, etc.
        extractor = TextExtractor()
        extractor.feed(resp.text)
        return extractor.get_text()
    elif 'application/pdf' in content_type or resp.content[:4] == b'%PDF':
        # Direct PDF URL
        p = RESUME_PATH
        p.write_bytes(resp.content)
        return read_pdf(p)
    else:
        # Plain text / markdown
        return resp.text

def refresh_cache():
    global cached_resume
    try:
        download_portfolio()

        # --- Strategy 1: RESUME_URL env var (Google Docs "Publish to web", Notion public page, etc.) ---
        # This is the recommended approach. It is live-connected: edit your doc, click Sync, done.
        # Set RESUME_URL on Render to your Google Docs publish link:
        #   Google Docs -> File -> Share -> Publish to web -> Publish -> copy link
        resume_url = os.getenv("RESUME_URL", "https://docs.google.com/document/d/e/2PACX-1vThRffUcE83s7RNij3h7XpNTYpu2Q90xxncdNfk6-SFcPCWR4lNRG1TeBR9-ZExXw/pub").strip()
        if resume_url:
            print(f"Fetching resume from RESUME_URL: {resume_url}")
            text = fetch_resume_from_url(resume_url)
            if text and len(text) > 200:
                cached_resume = parse_resume(text)
                print(f"Resume fetched live from URL ({len(text)} chars).")
                return
            else:
                print("WARNING: RESUME_URL returned too little content, trying fallbacks.")

        # --- Strategy 2: Google Drive PDF download (may be blocked on some cloud IPs) ---
        gdrive_id = os.getenv("RESUME_GDRIVE_ID", "1W-dn895-Z8SC5uT160ZejL5Ij28CsVZP")
        print("Trying Google Drive download...")
        session = requests.Session()
        for url in [
            f"https://drive.usercontent.google.com/download?id={gdrive_id}&export=download&authuser=0",
            f"https://drive.google.com/uc?export=download&confirm=t&id={gdrive_id}",
        ]:
            try:
                resp = session.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
                resp.raise_for_status()
                if resp.content[:4] == b'%PDF':
                    RESUME_PATH.write_bytes(resp.content)
                    text = read_pdf(RESUME_PATH)
                    cached_resume = parse_resume(text)
                    print(f"Resume fetched from Google Drive ({len(text)} chars).")
                    return
            except Exception as e:
                print(f"Google Drive URL failed: {e}")

        # --- Strategy 3: RESUME_TEXT env var (static fallback, set manually on Render) ---
        resume_text_env = os.getenv("RESUME_TEXT", "").strip()
        if resume_text_env:
            print("Using RESUME_TEXT fallback from environment variable.")
            cached_resume = parse_resume(resume_text_env)
            return

        print("WARNING: All resume sources failed. AI will respond without resume context.")
        cached_resume = None
    except Exception as e:
        print(f"Error during refresh_cache: {e}")
        cached_resume = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        refresh_cache()
    except Exception as e:
        print("ERROR during startup cache refresh:", e)
    yield

app=FastAPI(lifespan=lifespan)



#parse resume
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
resume_schema = Resume.model_json_schema()

class ChatRequest(BaseModel):
    question: str

def ask_candidate(question: str, resume: Resume):

    global cached_portfolio
    portfolio_context = cached_portfolio

    system_prompt = f"""
You are an AI assistant representing a job candidate.

Below is everything you know about the candidate from their parsed resume.

{resume.model_dump_json(indent=2)}

Below is additional context extracted directly from their live portfolio website (including their detailed projects, technical skills, and certifications). Use this information to supplement their resume.

{portfolio_context}

Rules:

1. Answer only using this information.

2. Never hallucinate.

3. If information is unavailable,
say

"I don't have enough information to answer that."

4. Be professional.

5. Answer as if HR is interviewing this candidate.
"""

    response = client.chat.completions.create(

        model=model,

        messages=[

            {
                "role":"system",
                "content":system_prompt
            },

            {
                "role":"user",
                "content":question
            }

        ]

    )

    return response.choices[0].message.content
def parse_resume(resume_text):
    system_prompt = f"""
    You are an expert resume parser.

    Extract information from the resume based on its meaning,
    not only based on exact section headings.

    Different resumes may use different headings.

    For example:
    - Experience
    - Professional Experience
    - Work History
    - Employment
    - Internships

    These may all contain relevant experience.

    Skills may also appear in the skills section, work experience,
    internships or projects.

    Return ONLY valid JSON matching this schema:

    {resume_schema}

    Important rules:

    1. Do not invent information.
    2. If a value is not available, return null.
    3. If a list has no information, return an empty list.
    4. Include internships inside experiences.
    5. Extract skills mentioned across the entire resume.
    """
    user_prompt = f"""
    Parse the following resume:

    {resume_text}
    """
    message_system={
        "role" : "system",
        "content" : system_prompt
    }
    message_user={
        "role" : "user",
        "content" : user_prompt
    }
    messages=[message_system, message_user]
    response_format={
        "type": "json_object"
    }
    response=client.chat.completions.create(model=model, messages=messages, response_format=response_format)
    raw_output = response.choices[0].message.content
    data = json.loads(raw_output)
    resume = Resume(**data)
    return resume

#pdf extraction
def read_pdf(file_path: Path):

    reader = PdfReader(file_path)

    text = ""

    for page in reader.pages:

        page_text = page.extract_text()

        if page_text:
            text += page_text + "\n"

    return text

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Mount the static directory using the absolute path so it works regardless of CWD
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
def home():
    # Serve the frontend UI using absolute path
    return FileResponse(str(STATIC_DIR / "index.html"))

# chatgpt.cpom
#chatgot.com/aceeddferre5e


@app.post("/chat")
def chat(request: ChatRequest):
    global cached_resume
    if not cached_resume:
        refresh_cache()
        
    if not cached_resume:
        return {
            "answer": "Sorry, I couldn't access my resume and portfolio context right now (Live sync failed). Please try again in a few minutes!"
        }
        
    answer=ask_candidate(request.question, cached_resume)
    return {
        "answer": answer
    }

from fastapi import HTTPException

@app.post("/refresh")
def refresh():
    refresh_cache()
    if not cached_resume:
        raise HTTPException(status_code=500, detail="Failed to parse or download resume.")
    return {"status": "success", "message": "Resume and portfolio cache have been refreshed dynamically!"}




# youtube.com
