import {useEffect,useRef,useState,type RefObject} from 'react';
import type {PersonPreview} from '../../api/generated';
import {nextOverlayPosition,overlayAt,overlayMaxAge} from './previewTimeline';

export function usePersonOverlay(video: RefObject<HTMLVideoElement | null>, source: string, confidence: number, revision: number) {
  const [sample,setSample] = useState<PersonPreview | null>(null);
  const [overlay,setOverlay] = useState<PersonPreview | null>(null);
  const [cvError,setCvError] = useState('');
  const inFlight = useRef(false);

  useEffect(()=>{
    let disposed = false, timer: ReturnType<typeof setTimeout>, animation = 0;
    let previous = -Infinity;
    let samples: PersonPreview[] = [];
    let shown: PersonPreview | null = null;
    let drawnSamples = samples, drawnPosition = -Infinity;
    setSample(null); setOverlay(null); setCvError('');
    if (!source) return;
    const schedule = (delay: number) => { if (!disposed) timer = setTimeout(inspect,delay); };
    function draw() {
      const player = video.current;
      const position = player && !player.seeking ? player.currentTime*1000 : -Infinity;
      if (position !== drawnPosition || samples !== drawnSamples) {
        const next = overlayAt(samples,source,confidence,position);
        if (next !== shown) { shown = next; setOverlay(next); }
        drawnPosition = position; drawnSamples = samples;
      }
      animation = requestAnimationFrame(draw);
    }
    async function inspect() {
      const player = video.current;
      if (!player || player.readyState < 2 || player.seeking || !Number.isFinite(player.duration) || inFlight.current) { schedule(80); return; }
      const position = Math.round(player.currentTime*1000);
      const target = nextOverlayPosition(position,Math.round(player.duration*1000),player.paused,previous);
      if (target === null) { schedule(80); return; }
      previous = target;
      inFlight.current = true;
      try {
        const response = await fetch('/api/preview/people',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({source_id:source,position_ms:target,confidence_threshold:confidence,include_image:false})});
        const result = await response.json();
        if (disposed) return;
        if (response.status === 429) { previous = -Infinity; setCvError('Detector busy. Retrying shortly…'); schedule(250); return; }
        if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Person detection failed.');
        setSample(result as PersonPreview); setCvError('');
        if (result.status !== 'available') { samples = []; setOverlay(null); return; }
        const now = (video.current?.currentTime ?? 0)*1000;
        samples = [...samples,result as PersonPreview].filter(s=>s.position_ms >= now-overlayMaxAge).slice(-12);
      } catch (error) {
        if (!disposed) { samples = []; setOverlay(null); setCvError(String(error)); }
        return;
      } finally { inFlight.current = false; }
      schedule(25);
    }
    animation = requestAnimationFrame(draw);
    schedule(150);
    return ()=>{disposed = true; clearTimeout(timer); cancelAnimationFrame(animation);};
  },[video,source,confidence,revision]);
  // Hide prior settings immediately, including before the effect cleanup runs.
  const current = overlay?.source_id === source && overlay.confidence_threshold === confidence ? overlay : null;
  return {sample,overlay:current,cvError};
}
