"""Compatibility command. Implementation: backend/app/trainer/cli/register.py."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.trainer.cli.register import main

if __name__=='__main__':main()
