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

from contextlib import asynccontextmanager
import urllib.request
import ssl
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
    def handle_data(self, data):
        text = data.strip()
        if text:
            self.text_parts.append(text)
    def get_text(self):
        return "\n".join(self.text_parts)

def download_portfolio():
    global cached_portfolio
    url = "https://shashank17singh.github.io"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    print("Downloading live portfolio...")
    try:
        with urllib.request.urlopen(req) as response:
            html_bytes = response.read()
            html_str = html_bytes.decode('utf-8')
            extractor = TextExtractor()
            extractor.feed(html_str)
            cached_portfolio = extractor.get_text()
        print("Portfolio downloaded and cached.")
    except Exception as e:
        print("Failed to download portfolio:", e)

def download_resume():
    gdrive_id = os.getenv("RESUME_GDRIVE_ID", "1W-dn895-Z8SC5uT160ZejL5Ij28CsVZP")
    url = f"https://drive.google.com/uc?export=download&id={gdrive_id}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    print("Downloading latest resume from Google Drive...")
    try:
        with urllib.request.urlopen(req, context=ctx) as response, open(RESUME_PATH, 'wb') as out_file:
            out_file.write(response.read())
        print("Download complete.")
    except Exception as e:
        print("Failed to download resume:", e)

def refresh_cache():
    global cached_resume
    try:
        download_resume()
        download_portfolio()
        
        # Make sure the file exists and is reasonably sized (Google Drive error pages are small, real PDFs are larger)
        if RESUME_PATH.exists() and RESUME_PATH.stat().st_size > 1000:
            resume_text = read_pdf(RESUME_PATH)
            cached_resume = parse_resume(resume_text)
            print("Resume and portfolio successfully parsed and cached in memory.")
        else:
            print("WARNING: Resume PDF could not be found or is an invalid file. AI will start without resume context.")
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
            "answer": "Sorry, I couldn't access my resume and portfolio context right now (Google Drive sync failed). Please try again in a few minutes!"
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
