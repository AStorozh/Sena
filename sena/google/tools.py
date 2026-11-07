"""All Google tools for Sena in one place. Sending mail (mail_send) is wired up in ag_sena/tools.py:
it goes through the shared "after your yes" confirmation."""
from sena.google import auth, drive, gmail, sheets

TOOLS = [*gmail.TOOLS, *sheets.TOOLS, *drive.TOOLS, *auth.TOOLS]
FUNCTIONS = {**gmail.FUNCTIONS, **sheets.FUNCTIONS, **drive.FUNCTIONS, **auth.FUNCTIONS}
