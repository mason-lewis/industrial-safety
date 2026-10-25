"""Start jobs; HTTP handlers never run decoder loops."""
from safety_app.domain.models import ReplayRequest
class Monitoring:
    def __init__(self,rt):self.rt=rt
    def replay_video(self,request:ReplayRequest):
        self.rt.camera(request.profile_id)
        return self.rt.jobs.start('replay',request.idempotency_key,request.model_dump())
    def start_monitoring(self,camera_id,key):
        self.rt.camera(camera_id)
        return self.rt.jobs.start('live',key,{'profile_id':camera_id,'source_id':'rtsp-1','start_ms':0,'end_ms':0})
