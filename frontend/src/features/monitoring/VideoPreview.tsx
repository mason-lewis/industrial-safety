import {useRef, useState, type DragEvent} from 'react';
import {useVideoPreview} from './useVideoPreview';
import LivePersonOverlay from './LivePersonOverlay';
import VideoThumbnail from '../../components/VideoThumbnail';
import PersonConfidence from '../../components/PersonConfidence';
import Icon from '../../components/ReviewIcon';
import {videoTitle as title} from '../../components/reviewPresentation';
import '../../components/reviewRun.css';
import './videoPreview.css';

export default function VideoPreview({onHelp}: {onHelp: () => void}) {
  const p = useVideoPreview();
  const file = useRef<HTMLInputElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  async function fullscreen() {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else await stage.current?.requestFullscreen(); }
    catch { p.setError('Full screen is unavailable in this browser.'); }
  }
  const [query, setQuery] = useState('');
  const selected = p.sources.find(source => source.id === p.selected);
  const featured = p.sources[0];
  const drop = {
    onDragOver: (e: DragEvent) => {e.preventDefault(); setDragging(true);},
    onDragLeave: (e: DragEvent<HTMLElement>) => {if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false);},
    onDrop: (e: DragEvent) => {e.preventDefault(); setDragging(false); const picked = e.dataTransfer.files[0]; if (picked) void p.upload(picked);},
  };
  const dropCard = (compact: boolean) => <section className={`drop-card${dragging ? ' is-dragging' : ''}`} aria-label="Open a video" {...drop}>
    <span className="hazard-stripe"/>
    <span className="drop-symbol"><Icon name="upload"/></span>
    {compact ? <h2>{dragging ? 'Drop it' : 'Drop footage'}</h2> : <h1>{dragging ? 'Drop it' : <>Drop<br/>footage</>}</h1>}
    <div className="drop-card-actions"><button className="solid-button" disabled={p.uploading} onClick={() => file.current?.click()}><Icon name="upload"/>{p.uploading ? 'Uploading…' : 'Open video'}</button></div>
  </section>;
  return <main className="consumer-workspace preview-workspace" aria-label="Video preview">
    <div className="workspace-topline"><span className="chip chip-dark"><Icon name="eye"/>Local CV · No AI review starts here.</span><button className="text-button" onClick={onHelp}><Icon name="help"/>How to use</button></div>
    <input hidden ref={file} type="file" accept=".mp4,.mov,.mkv,.avi,.webm,.m4v" disabled={p.uploading}
      onChange={event => {const picked = event.target.files?.[0]; if (picked) void p.upload(picked); event.target.value = '';}}/>
    {p.error && <p className="consumer-error" role="alert">{p.error}</p>}
    {selected ? <div className="preview-grid">
      <section className="preview-playback player-card" aria-label="Video preview player">
        <div className="cinema"><div className="cinema-screen" ref={stage}>
          <video key={selected.id} ref={p.video} controls playsInline preload="metadata" controlsList="nofullscreen" disablePictureInPicture
            src={`/api/preview/${encodeURIComponent(selected.id)}/video`} onLoadedMetadata={p.inspect}
            onSeeking={p.inspect} onSeeked={p.inspect} onError={() => p.setError('This format cannot play in the browser. Try an H.264 MP4 file.')}/>
          <LivePersonOverlay video={p.video} source={selected.id} revision={p.revision} onFullscreen={() => void fullscreen()}/>
        </div></div>
        <div className="preview-video-caption"><strong title={selected.name}>{title(selected.name)}</strong></div>
      </section>
      <aside className="preview-side">{dropCard(true)}<div className="preview-confidence"><PersonConfidence preview compact/></div></aside>
    </div> : <div className="home-grid">
      {dropCard(false)}
      <div className="hero-side">
        {featured ? <button className="feature-tile" onClick={() => p.choose(featured.id)} disabled={p.uploading}>
          <VideoThumbnail sourceId={featured.id}/><span className="chip chip-yellow"><Icon name="eye"/>Preview</span>
          <span className="feature-name">{title(featured.name)}</span><span className="feature-play"><Icon name="play"/></span></button>
          : <div className="feature-tile is-empty"><Icon name="video"/></div>}
        <div className="preview-confidence"><PersonConfidence preview compact/></div>
      </div>
    </div>}
    <section className="library-section" aria-label="Preview video library">
      <div className="library-heading"><h2>Your videos <span>{p.sources.length}</span></h2>
        <input aria-label="Find a preview video" placeholder="Find a video…" value={query} onChange={event => setQuery(event.target.value)}/></div>
      <div className="thumb-grid">{p.sources.filter(source => source.name.toLowerCase().includes(query.toLowerCase())).map(source =>
        <button className="thumb-tile" key={source.id} aria-pressed={p.selected === source.id} disabled={p.uploading} title={source.name} onClick={() => p.choose(source.id)}>
          <VideoThumbnail sourceId={source.id}/><strong>{title(source.name)}</strong><span className="thumb-play"><Icon name="play"/></span></button>)}</div>
      {!p.sources.length && <div className="library-empty"><Icon name="upload"/>Open a video to add it to your library.</div>}
    </section>
  </main>;
}
