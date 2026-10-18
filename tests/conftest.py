"""Point the app at a throwaway DB before anything imports app.config."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
