import {useId} from 'react';
import {useReviewPreferences} from '../hooks/useReviewPreferences';
import PersonConfidence from './PersonConfidence';

export default function ReviewSettings({request}: {request?: Record<string, unknown>}) {
  const preferences = useReviewPreferences();
  const id = useId();
  const jurisdiction = request ? request.jurisdiction : preferences.jurisdiction;
  const ppe = request && Array.isArray(request.required_ppe)
    ? request.required_ppe.filter((item): item is string => typeof item === 'string').join(', ')
    : '';

  return <section className={`review-settings${request ? ' saved-review-settings' : ''}`} aria-labelledby={`${id}-title`}>
    <div className="review-settings-heading">
      <h2 id={`${id}-title`}>{request ? 'Settings used' : 'Review settings'}</h2>
      <span>{request ? 'Saved with this review' : 'Optional'}</span>
    </div>
    {request ? <dl className="review-settings-fields">
      <div><dt>Required protective equipment</dt><dd>{ppe || (Array.isArray(request.required_ppe) ? 'Not specified' : 'Not recorded')}</dd></div>
      <div><dt>Safety references</dt><dd>{jurisdiction === 'US-federal-OSHA' ? 'U.S. OSHA references' : jurisdiction === 'unknown' ? 'Location not specified' : typeof jurisdiction === 'string' ? jurisdiction : 'Not recorded'}</dd></div>
      <div><dt>Person detection confidence</dt><dd>{typeof request.person_confidence === 'number' ? `${Math.round(request.person_confidence * 100)}%` : '50% (original default)'}</dd></div>
    </dl> : <div className="review-settings-fields">
      <label htmlFor={`${id}-ppe`}><span id={`${id}-ppe-label`}>Required protective equipment</span>
        <input id={`${id}-ppe`} value={preferences.ppe} onChange={e => preferences.setPpe(e.target.value)} placeholder="e.g. hard hat, safety vest" aria-labelledby={`${id}-ppe-label`} aria-describedby={`${id}-ppe-help`}/>
        <small id={`${id}-ppe-help`}>Comma-separated. Blank if unknown.</small>
      </label>
      <label htmlFor={`${id}-references`}><span id={`${id}-references-label`}>Safety references</span>
        <select id={`${id}-references`} value={preferences.jurisdiction} onChange={e => preferences.setJurisdiction(e.target.value === 'US-federal-OSHA' ? 'US-federal-OSHA' : 'unknown')} aria-labelledby={`${id}-references-label`} aria-describedby={`${id}-reference-help`}>
          <option value="unknown">Location not specified</option>
          <option value="US-federal-OSHA">U.S. OSHA references</option>
        </select>
        <small id={`${id}-reference-help`}>{jurisdiction === 'US-federal-OSHA' ? 'Federal rules and guidance. Applicability still needs checking.' : 'Unsure? Leave as is.'}</small>
      </label>
    </div>}
    {!request && <PersonConfidence/>}
    {jurisdiction === 'US-federal-OSHA' && <p className="reference-explanation">OSHA is the U.S. workplace safety agency. This lets the AI consult federal sources; it does not confirm a violation or cover state-specific rules. <a href="https://www.osha.gov/laws-regs" target="_blank" rel="noreferrer">About OSHA references ↗</a></p>}
    <p className="settings-budget">Limits: <strong>${(typeof request?.max_cost_usd === 'number' ? Math.min(.15, request.max_cost_usd) : .15).toFixed(2)} per run</strong><span> · $2 per day · $20 per month</span></p>
  </section>;
}
