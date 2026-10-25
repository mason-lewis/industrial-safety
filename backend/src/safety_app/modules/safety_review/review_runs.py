"""Durable coverage manifests, shared budgets, recovery and human disposition."""
import copy
import json
from safety_app.domain.models import HumanDecision, MediaRef, utc
from safety_app.domain.review_runs import ReviewRun, ReviewRunStatus, ReviewWindow
from safety_app.modules.monitoring.recordings import describe, resolve


def consecutive_windows(start, end, width=10000):
    if not 0 <= start < end or width <= 0:
        raise ValueError('Expected a positive source interval and window width')
    return [(lo, min(end, lo + width)) for lo in range(start, end, width)]


def update_record(rt, record_kind, id, **changes):
    """Merge under a write lock so player progress cannot overwrite cancellation."""
    with rt.db.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (record_kind, id)).fetchone()
        if not row:
            raise KeyError(id)
        body = json.loads(row['body'])
        body.update(changes, updated_at=utc())
        db.execute('UPDATE objects SET body=? WHERE kind=? AND id=?', (json.dumps(body), record_kind, id))
    return body


class ReviewRuns:
    def __init__(self, rt):
        self.rt = rt

    def get(self, id, site_id):
        run = ReviewRun.model_validate(self.rt.db.get('review_run', id))
        if run.site_id != site_id:
            raise ValueError('Review run outside site')
        return run

    def windows(self, id):
        return sorted((ReviewWindow.model_validate(row) for row in self.rt.db.list('review_window')
                       if row['run_id'] == id), key=lambda w: w.index)

    def prepare(self, job, body, kind='review', session_id=None, generation=0):
        try:
            return self.get(job.run_id, job.site_id)
        except KeyError:
            pass
        path = resolve(self.rt.root, body.source_id)
        digest, first, endpoint, _ = describe(path)
        start = max(first, body.start_ms)
        end = endpoint if body.end_ms is None else body.end_ms
        if not first <= start < end <= endpoint:
            raise ValueError('Requested interval is outside the recorded source PTS range')
        if end - start > 600000:
            raise ValueError('Full review supports at most ten minutes per recording interval')
        config = copy.deepcopy(self.rt.config)
        config['limits']['workflow_usd'] = min(.15, body.max_cost_usd or .15, config['limits']['workflow_usd'])
        config['limits']['day_usd'] = min(2, config['limits']['day_usd'])
        config['limits']['month_usd'] = min(20, config['limits']['month_usd'])
        config['review_team']['max_workflow_requests'] = 256
        run = ReviewRun(id=job.run_id, job_id=job.id, site_id=job.site_id,
                        source_id=body.source_id, source_hash=digest, media_id=job.run_id,
                        camera_id=body.camera_id, start_ms=start, end_ms=end, cutoff_ms=start,
                        kind=kind, mode=body.mode, configuration=config,
                        request=body.model_dump(mode='json'), session_id=session_id, generation=generation)
        if body.mode == 'fixture':
            run.limitations.insert(0, 'FIXTURE: fixed synthetic responses; no visual recognition, real safety assessment or paid model call.')
        media = MediaRef(id=run.media_id, media_id=run.media_id, source_id=run.source_id,
                         camera_id=run.camera_id, start_ms=start, end_ms=end,
                         content_hash=digest, storage_key='source:' + run.source_id,
                         capture_time_utc=body.recording_start_utc.isoformat() if body.recording_start_utc else None)
        windows = [ReviewWindow(id=f'{run.id}:window:{i}', run_id=run.id, index=i, start_ms=lo, end_ms=hi)
                   for i, (lo, hi) in enumerate(consecutive_windows(start, end))]
        records=[('media',media),('review_run',run),*[('review_window',w) for w in windows]]
        with self.rt.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute("SELECT body FROM objects WHERE kind='review_run' AND id=?",(run.id,)).fetchone()
            if old:return ReviewRun.model_validate_json(old['body'])
            for kind,record in records:
                db.execute('INSERT INTO objects VALUES(?,?,?)',(kind,record.id,record.model_dump_json()))
        with self.rt.db.connect() as db:
            existing = self.rt.jobs.get(job.id)
            existing.review_run_id = run.id
            db.execute('UPDATE jobs SET body=? WHERE id=?', (existing.model_dump_json(), job.id))
        self.rt.audit.write('review_manifest_created', job_id=job.id, run_id=run.id,
                            output={'windows': len(windows), 'source_hash': digest, 'kind': kind})
        return run

    def usage(self, run):
        with self.rt.db.connect() as db:
            rows = db.execute('SELECT * FROM reservations WHERE workflow_id=?', (run.id,)).fetchall()
        spent = sum(row['actual'] or 0 for row in rows) / 1e6
        reserved = sum(row['amount'] for row in rows if row['actual'] is None) / 1e6
        with self.rt.audit.db.connect() as db:
            events = [json.loads(r['body']) for r in db.execute(
                "SELECT body FROM audit WHERE json_extract(body,'$.parent_run_id')=? AND json_extract(body,'$.event_type') IN ('model_request','model_response')", (run.id,))]
        responses = [e.get('resources', {}) for e in events if e['event_type'] == 'model_response'
                     and not e.get('resources',{}).get('usage_is_synthetic')]
        keys = ('input_tokens', 'uncached_input_tokens', 'cached_input_tokens', 'cache_write_tokens',
                'cache_write_30m_tokens', 'cache_write_12h_tokens', 'output_tokens')
        return {'limit_usd': run.configuration['limits']['workflow_usd'],
                'usage_cost_usd': spent, 'reserved_usd': reserved, 'billed_cost_usd': None,
                'remaining_usd': max(0, run.configuration['limits']['workflow_usd'] - spent - reserved),
                'unknown_cost_requests': sum(row['actual'] is None for row in rows),
                'physical_requests': len(rows), 'fixture': run.mode == 'fixture',
                **{key: sum(r.get(key) or 0 for r in responses) for key in keys},
                'pricing': run.configuration['pricing'],
                'basis': 'Calculated from reported tokens; outstanding reservations are separate; not a provider invoice.'}

    def status(self, id, site_id):
        from safety_app.modules.safety_review.video_cases import overview
        run = self.get(id, site_id)
        windows = self.windows(id)
        parent = self.rt.jobs.get(run.job_id)
        if run.kind == 'review':
            state = 'ended' if parent.status == 'completed' else parent.status
            run.state = 'budget_exceeded' if state == 'budget_deferred' else state
        # A vanished worker must not leave a window displayed as actively running.
        for window in windows:
            owner = self.rt.jobs.get(window.job_id or run.job_id)
            if window.status in ('queued','running') and owner.status not in ('running', 'queued'):
                window.status = 'cancelled' if owner.status == 'cancelled' else 'failed'
                window.error = owner.error.message if owner.error else 'Processing ended before checkpoint'
        safety = overview(self.rt, id, site_id)
        targets = {case.case.id for case in safety.cases}
        decisions = [HumanDecision.model_validate(d) for d in self.rt.db.list('decision') if d['target_id'] in targets]
        complete = bool(windows) and run.source_verified and all(w.status == 'completed' and not w.gaps for w in windows)
        pending = sum(case.assessment is None or bool(case.unassessed_screening_ids) for case in safety.cases)
        usage = self.usage(run)
        reason = ''
        if run.kind == 'live': reason = 'Use camera playback controls; past live gaps are retained.'
        elif parent.status in ('queued', 'running'): reason = 'Review is already active.'
        elif usage['unknown_cost_requests']: reason = 'Reconcile outstanding or uncertain charges before resuming.'
        elif usage['remaining_usd'] <= 0: reason = 'Original run budget exhausted; resume never renews the allowance.'
        elif self.rt.budgets.state('stopped') == '1': reason = 'The global stop switch is on.'
        elif not complete and all(w.status=='completed' for w in windows):
            reason = 'All windows have outcomes, but sampling or provenance gaps remain. These records cannot be called complete.'
        elif complete and not any(c.review_job and c.review_job.status in ('failed','interrupted','cancelled','budget_exceeded') for c in safety.cases):
            reason = 'All windows inspected. Queued assessments continue; evidence outside bounded assessment packets remains explicit for human review.'
        from safety_app.infrastructure.review_allowances import snapshot
        test_budget=snapshot(self.rt.budgets,id)
        if test_budget and test_budget['closed']:reason='This single funded test is finished. Its unused allowance is not available for automatic retries.'
        diagnostics=[]
        for row in self.rt.audit.rows(1000,parent_run_id=id):
            resources=row.get('resources',{})
            if resources.get('transport'):
                diagnostics.append(resources['transport'] | {'reservation_id':resources.get('reservation_id'),
                                   'provider_response_id':resources.get('provider_request_id')})
        details=[d for d in self.rt.db.list('temporal_detail') if d['run_id']==id]
        return ReviewRunStatus(run=run, windows=windows, coverage_complete=complete,
            covered_ms=sum(w.end_ms-w.start_ms for w in windows if w.status == 'completed' and not w.gaps),
            expected_ms=run.end_ms-run.start_ms, completed_windows=sum(w.status == 'completed' and not w.gaps for w in windows),
            total_windows=len(windows), specialist_complete=complete and pending == 0,
            pending_specialists=pending, human_disposition_complete=complete and targets <= {d.target_id for d in decisions},
            safety=safety, decisions=decisions, usage=usage, resume_allowed=not reason, resume_reason=reason,
            people=self.rt.people.for_run(run),transport_diagnostics=diagnostics,
            detail_inspections=details,test_budget=test_budget)

    def cancel(self, id, site_id):
        run = self.get(id, site_id)
        self.rt.jobs.cancel(run.job_id)
        with self.rt.db.connect() as db:
            rows = db.execute("SELECT id FROM jobs WHERE status IN ('queued','running') AND (json_extract(body,'$.payload.review_run_id')=? OR json_extract(body,'$.payload.analysis_id')=?)", (id, id)).fetchall()
        for row in rows: self.rt.jobs.cancel(row['id'])
        update_record(self.rt, 'review_run', id, state='cancelled')
        return self.status(id, site_id)

    def resume(self, id, site_id):
        status = self.status(id, site_id)
        if not status.resume_allowed: raise ValueError(status.resume_reason)
        run = status.run
        digest, _, _, _ = describe(resolve(self.rt.root, run.source_id))
        if digest != run.source_hash: raise ValueError('Source content changed; cannot resume this evidence scope')
        if status.coverage_complete:
            for case in status.safety.cases:
                if case.review_job and case.review_job.status in ('failed','interrupted','cancelled','budget_exceeded'):
                    self.rt.jobs.resume(case.review_job.id)
        else:
            self.rt.jobs.resume(run.job_id)
            update_record(self.rt, 'review_run', id, state='queued')
        return self.status(id, site_id)

    def decide(self, id, body, user):
        status = self.status(id, user.site_id)
        if body.target_id not in {c.case.id for c in status.safety.cases}:
            raise ValueError('Decision target outside this run')
        decision = HumanDecision(target_id=body.target_id, decision=body.decision,
                                 reason=body.reason, reviewer_id=user.id)
        self.rt.db.save('decision', decision, immutable=True)
        self.rt.audit.write('human_decision', run_id=id, output=decision.model_dump(mode='json'))
        return decision
