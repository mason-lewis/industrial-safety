"""Facility review shares bounded stages. Sources and site context remain explicit."""
from safety_app.workflows.event_review import review
async def facility_review(rt,job,model_factory=None):return await review(rt,job,model_factory)
