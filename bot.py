"""
Backward-compatible entrypoint.

Preferred:
    python -m bot.main

This file remains so `python bot.py` and `run.bat` keep working.
"""
from bot.main import main

if __name__ == "__main__":
    main()
