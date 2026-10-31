import os
import sys
from pathlib import Path

# The Groq client is created at import time and needs a value. Tests never call it.
os.environ.setdefault("GROQ_API_KEY", "placeholder")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
