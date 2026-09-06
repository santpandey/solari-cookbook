import os
import subprocess
import sys

base = os.path.dirname(os.path.abspath(__file__))

if sys.platform == "win32":
    uvicorn = os.path.join(base, ".venv", "Scripts", "uvicorn.exe")
else:
    uvicorn = os.path.join(base, ".venv", "bin", "uvicorn")

if not os.path.exists(uvicorn):
    print("venv uvicorn not found.")
    print("Run: .venv\\Scripts\\python -m pip install -r requirements.txt")
    sys.exit(1)

subprocess.run(
    [uvicorn, "main:app", "--host", "127.0.0.1", "--port", "8000"],
    cwd=base,
)
