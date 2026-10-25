"""One predictable dotenv policy for the API, workers and launcher."""
import os
from pathlib import Path
from dotenv import dotenv_values


def load_environment(project_root=None):
    root=Path(project_root) if project_root else Path(__file__).resolve().parents[4]
    # Existing process values, including intentional empty values in tests, win.
    for path in (root/'.env',root.parent/'.env'):
        for key,value in dotenv_values(path).items():
            if value is not None and value.strip() and key not in os.environ:
                os.environ[key]=value


def workspace_root():
    return Path(os.environ.get('SAFETY_WORKSPACE',Path(__file__).resolve().parents[4])).resolve()
