"""Elapsed time measurements use a monotonic clock; audit timestamps use UTC."""
from contextlib import contextmanager
from time import monotonic
from safety_app.domain.models import ToolFailure
@contextmanager
def stage(audit,name,**identity):
    start=monotonic();audit.write('stage_started',status='running',workflow_name=name,**identity)
    try:yield
    except Exception as exc:
        audit.write('stage_finished',status='failed',workflow_name=name,duration_ms=(monotonic()-start)*1000,error=ToolFailure(code=type(exc).__name__,message=str(exc)),**identity);raise
    else:audit.write('stage_finished',workflow_name=name,duration_ms=(monotonic()-start)*1000,**identity)
