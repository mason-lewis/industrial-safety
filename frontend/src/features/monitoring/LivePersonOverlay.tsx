import type {RefObject} from 'react';
import {useReviewPreferences} from '../../hooks/useReviewPreferences';
import {personCount} from '../../components/reviewPresentation';
import {usePersonOverlay} from './usePersonOverlay';
import PersonOverlay from './PersonOverlay';

// Keep animation updates inside the player, away from library/slider rendering.
export default function LivePersonOverlay({video,source,revision,onFullscreen,confidence}: {
  video: RefObject<HTMLVideoElement | null>; source: string; revision: number; confidence?:number; onFullscreen: () => void;
}) {
  const {personConfidence} = useReviewPreferences();
  const {sample,overlay,cvError} = usePersonOverlay(video,source,confidence??personConfidence,revision);
  const unavailable = sample && sample.status !== 'available';
  const error = cvError || (unavailable ? sample.detail || 'Person detection is unavailable.' : '');
  return <>
    <PersonOverlay sample={overlay}/>
    <div className="preview-player-actions"><span>{overlay ? `CV · ${personCount(overlay.people?.length ?? 0)}` : error ? 'CV unavailable' : 'CV · checking…'}</span>
      <button onClick={onFullscreen} title="Toggle full screen">Full screen</button></div>
    {error && <p className="preview-cv-error" role="alert">{error}</p>}
  </>;
}
