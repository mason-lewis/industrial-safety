"""Repeat the recorded-video harness on a synthetic MP4 in a fresh temp root."""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/src')]


async def main():
    import cv2
    import numpy as np
    from safety_app.domain.video_analysis import VideoAnalysisRequest
    from safety_app.infrastructure.runtime import Runtime
    from safety_app.modules.monitoring.recordings import sources
    from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
    from safety_app.workers.runner import execute

    folder = Path(tempfile.mkdtemp(prefix='video-analysis-fixture-'))
    shutil.copytree(ROOT / 'configs', folder / 'configs')
    (folder / 'data/input').mkdir(parents=True)
    writer = cv2.VideoWriter(str(folder / 'data/input/fixture.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64, 64))
    if not writer.isOpened():
        raise RuntimeError('Synthetic MP4 writer is unavailable')
    for index in range(400):
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        image[:, :, index // 134 % 3] = 160
        writer.write(image)
    writer.release()
    rt = Runtime(folder)
    body = VideoAnalysisRequest(source_id=sources(folder)[0].id,
                                question='Demonstrate recorded-video inspection and verification.',
                                mode='fixture', idempotency_key='synthetic-video-analysis')
    job = VideoAnalysisService(rt).request(body, 'local')
    finished = await execute(rt, job)
    children=[child for child in rt.jobs.list() if child.kind=='video_safety_review']
    for child in children:
        child_result=await execute(rt,child)
        if child_result.status!='completed':raise RuntimeError(str(child_result.error))
    rows = [row for id in [job.id,*[child.id for child in children]] for row in rt.audit.rows(1000, job_id=id)]
    calls = [row for row in rows if row['event_type'] == 'model_request']
    report = {'data_kind': 'synthetic_fixture_only', 'root': str(folder), 'job_status': finished.status,
              'error': finished.error.model_dump() if finished.error else None,
              'result_refs': finished.result_refs, 'observed_chunks': len(rt.db.list('video_chunk')),
              'model_requests': len(calls), 'provider_requests': sum(not row['configuration']['fake_provider'] for row in calls),
              'independent_verification': any(row['agent_name'] == 'challenge_reviewer' for row in calls)}
    report.update(safety_review_jobs=len(children),safety_assessments=len(rt.db.list('video_safety_assessment')),
                  provisional_manager_attention=len(rt.db.list('manager_attention')))
    (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if finished.status != 'completed' or report['provider_requests']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
