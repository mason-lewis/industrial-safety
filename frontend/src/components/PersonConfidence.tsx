import {useId} from 'react';
import {useReviewPreferences} from '../hooks/useReviewPreferences';

export default function PersonConfidence({preview = false, compact = false}: {preview?: boolean; compact?: boolean}) {
  const {personConfidence, setPersonConfidence} = useReviewPreferences();
  const id = useId();
  return <div className="person-confidence">
    <label htmlFor={id} title={compact ? 'Lower settings find more people with more false positives. Scores are not a guarantee of accuracy.' : undefined}>Person detection confidence <output htmlFor={id}>{Math.round(personConfidence * 100)}%</output></label>
    <input id={id} type="range" min="5" max="95" step="5" value={Math.round(personConfidence * 100)}
      aria-describedby={compact ? undefined : `${id}-help`} aria-valuetext={`${Math.round(personConfidence * 100)} percent`}
      onChange={event => setPersonConfidence(Number(event.target.value) / 100)}/>
    {!compact && <><div className="confidence-scale"><span>More detections</span><span>Stronger matches</span></div>
    <p id={`${id}-help`}>Lower values can find more people but add false positives. Scores are not a guarantee of accuracy. {preview ? 'Updates the CV preview and your next review.' : 'Applies to your next Live or Review run.'}</p></>}
  </div>;
}
