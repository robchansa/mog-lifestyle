"""Entry point for Passenger -- Namecheap / cPanel "Setup Python App".

cPanel's Python app is pointed at this file and calls `application` for every
request.  No secrets live here or anywhere in the code folder (it is replaced
on every deploy): settings come from the private env file, by default
`~/mog-data/mog.env` -- see docs/NAMECHEAP.md.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.envfile import load_env_file  # noqa: E402  (reads no configuration)

load_env_file()

from app.wsgi import application  # noqa: E402,F401
