import os
import sys

# Make `import app...` work when pytest is run from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# llm_client builds a Groq client at import time; the tests never call it.
os.environ.setdefault("GROQ_API_KEY", "test-key-not-used")
