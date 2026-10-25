from safety_app.domain.models import ReviewRequest,HumanDecision,Finding
class SafetyReview:
    def __init__(self,rt):self.rt=rt
    def request_review(self,request:ReviewRequest,site_id):
        media_ids=request.media_ids
        if request.event_id:
            event=self.rt.db.get('event',request.event_id)
            if event['site_id']!=site_id:raise ValueError('Event outside site')
            media_ids=event['media_ids']
        for mid in media_ids:
            media=self.rt.db.get('media',mid)
            if self.rt.camera(media['camera_id']).site_id!=site_id:raise ValueError('Media outside site')
        if not media_ids:raise ValueError('No retained event evidence. Import retained evidence first')
        payload=request.model_dump(mode='json',exclude={'idempotency_key'})|{'media_ids':media_ids}
        return self.rt.jobs.start('event_review' if request.event_id else 'facility_review',request.idempotency_key,payload,site_id)
    def save_human_decision(self,target_id,decision,reason,reviewer_id,site_id):
        try:
            f=self.rt.db.get('finding',target_id)
            reviews=[r for r in self.rt.db.list('review') if r['id']==f['review_id']]
            if not reviews or reviews[0]['site_id']!=site_id:raise ValueError('Finding outside site')
        except KeyError:
            event=self.rt.db.get('event',target_id)
            if event['site_id']!=site_id:raise ValueError('Event outside site')
        value=HumanDecision(target_id=target_id,decision=decision,reason=reason,reviewer_id=reviewer_id)
        self.rt.audit.write('human_decision',event_id=target_id,output=value.model_dump(mode='json'));self.rt.db.save('decision',value,immutable=True);return value
