"""Stock Screener Launcher

Double-click to start. Auto-installs missing packages.
"""
import subprocess
import sys
import os
import shutil

os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("=" * 40)
print("  Stock Screener")
print("=" * 40)
print()

# Auto-install missing packages
REQUIRED = ["streamlit", "yfinance", "pandas", "numpy", "plotly", "requests",
            "bs4", "sqlalchemy", "yaml", "openpyxl", "xlrd", "sklearn"]

missing = []
for pkg in REQUIRED:
    try:
        __import__(pkg)
    except ImportError:
        missing.append(pkg)

if missing:
    print(f"Installing missing packages: {', '.join(missing)}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"])
    print("Install complete")
    print()

print(f"Python: {sys.version.split()[0]} ... OK")

# Start Ollama (optional)
if shutil.which("ollama"):
    try:
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags)
        print("Ollama: started")
    except Exception:
        print("Ollama: fallback mode")
else:
    print("Ollama: not installed (optional)")

print()
print("Starting... browser will open automatically")
print("Close this window to stop")
print()

sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", "app.py"]))
