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

cached_resume = None

def download_resume():
    gdrive_id = os.getenv("RESUME_GDRIVE_ID", "1W-dn895-Z8SC5uT160ZejL5Ij28CsVZP")
    url = f"https://drive.google.com/uc?export=download&id={gdrive_id}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    print("Downloading latest resume from Google Drive...")
    try:
        with urllib.request.urlopen(req, context=ctx) as response, open(Path("my_resume.pdf"), 'wb') as out_file:
            out_file.write(response.read())
        print("Download complete.")
    except Exception as e:
        print("Failed to download resume:", e)

def refresh_cache():
    global cached_resume
    download_resume()
    resume_text = read_pdf(Path("my_resume.pdf"))
    cached_resume = parse_resume(resume_text)
    print("Resume successfully parsed and cached in memory.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    refresh_cache()
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

    # Load additional context from the live portfolio if it exists
    portfolio_context = ""
    portfolio_file = Path("portfolio_context.txt")
    if portfolio_file.exists():
        with open(portfolio_file, "r", encoding="utf-8") as f:
            portfolio_context = f.read()

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

# Mount the static directory for CSS/JS if needed later
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def home():
    # Serve the frontend UI instead of raw JSON
    return FileResponse("static/index.html")

# chatgpt.cpom
#chatgot.com/aceeddferre5e


@app.post("/chat")
def chat(request: ChatRequest):
    global cached_resume
    if not cached_resume:
        refresh_cache()
    answer=ask_candidate(request.question, cached_resume)
    return {
        "answer": answer
    }

@app.post("/refresh")
def refresh():
    refresh_cache()
    return {"status": "success", "message": "Resume cache has been refreshed from Google Drive!"}




# youtube.com
