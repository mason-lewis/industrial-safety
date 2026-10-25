"""Temporal rule engine plus stable duplicate merge."""
from app.rules.engine import RuleEngine
from safety_app.domain.models import CandidateEvent

def merge_event(db,event:CandidateEvent,cooldown_ms=30000):
    for old in db.list('event'):
        if old['camera_id']==event.camera_id and old['event_type']==event.event_type and set(old['media_ids'])==set(event.media_ids) and (max(old['start_ms'],event.start_ms)<=min(old['end_ms'],event.end_ms) or (old.get('track_ids')==event.track_ids and event.track_ids and event.start_ms<=old['end_ms']+cooldown_ms)):
            old['end_ms']=max(old['end_ms'],event.end_ms);old['observation_ids']=list(dict.fromkeys(old['observation_ids']+event.observation_ids));db.save('event',old);return CandidateEvent.model_validate(old)
    db.save('event',event);return event
