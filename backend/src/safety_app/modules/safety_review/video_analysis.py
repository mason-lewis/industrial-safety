"""Explicit question submission; analysis runs in the existing review worker."""
from safety_app.domain.video_analysis import VideoAnalysis
from safety_app.modules.monitoring.recordings import resolve


class VideoAnalysisService:
    def __init__(self, rt):
        self.rt = rt

    def request(self, body, site_id):
        from safety_app.infrastructure.ai_control import require_hosted
        require_hosted(self.rt, body.mode)
        if self.rt.camera(body.camera_id).site_id != site_id:
            raise ValueError('Camera outside site')
        resolve(self.rt.root, body.source_id)
        job = self.rt.jobs.start('video_analysis', body.idempotency_key, body.model_dump(mode='json'), site_id)
        if body.review_strategy == 'full':
            from safety_app.modules.safety_review.review_runs import ReviewRuns
            try:
                ReviewRuns(self.rt).prepare(job, body)
            except Exception:
                from safety_app.domain.models import ToolFailure
                self.rt.jobs.finish(job, 'failed', error=ToolFailure(code='invalid_source', message='Cannot prepare full review source'))
                raise
            job = self.rt.jobs.get(job.id)
        return job

    def list(self, site_id):
        return [VideoAnalysis.model_validate(row) for row in self.rt.db.list('video_analysis')
                if row['site_id'] == site_id]

    def get(self, analysis_id, site_id):
        result = VideoAnalysis.model_validate(self.rt.db.get('video_analysis', analysis_id))
        if result.site_id != site_id:
            raise ValueError('Analysis outside site')
        return result
