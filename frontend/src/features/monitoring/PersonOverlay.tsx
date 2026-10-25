import type {PersonPreview} from '../../api/generated';
import {useEffect,useRef,useState} from 'react';
import {personCount} from '../../components/reviewPresentation';

export default function PersonOverlay({sample}: {sample: PersonPreview | null}) {
  const width = Number(sample?.provenance?.input_width) || 1920;
  const height = Number(sample?.provenance?.input_height) || 1080;
  const svg = useRef<SVGSVGElement>(null);
  const [fontSize,setFontSize] = useState(width/65);
  const visible = Boolean(sample && sample.status === 'available');
  useEffect(()=>{
    const element = svg.current;
    if (!element) return;
    const resize = () => {
      const rect = element.getBoundingClientRect();
      const scale = Math.min(rect.width/width,rect.height/height);
      if (scale > 0) setFontSize((rect.width < 500 ? 9 : 11)/scale);
    };
    const observer = new ResizeObserver(resize); observer.observe(element); resize();
    return ()=>observer.disconnect();
  },[width,height,visible]);
  if (!sample || !visible) return null;
  return <svg ref={svg} className="live-person-overlay" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet"
    role="img" aria-label={`${personCount(sample.people?.length ?? 0)} detected`} data-position-ms={sample.position_ms}>
    {sample.people?.map(person => {
      const [x,y,w,h] = person.box;
      const labelY = Math.max(fontSize+8,y*height);
      const labelWidth = fontSize*3.2;
      const labelX = Math.min(x*width, Math.max(0,width-labelWidth));
      return <g key={person.id}>
        <title>{`Person · ${Math.round(person.confidence*100)}%`}</title>
        <rect x={x*width} y={y*height} width={w*width} height={h*height} fill="none" stroke="#ffd400" strokeWidth="2" vectorEffect="non-scaling-stroke"/>
        <rect x={labelX} y={labelY-fontSize-8} width={labelWidth} height={fontSize+8} rx="3" fill="#111111e6"/>
        <text x={labelX+4} y={labelY-5} style={{fill:"#ffd400",fontSize,stroke:"none",letterSpacing:0,fontFamily:"system-ui, sans-serif"}}>{Math.round(person.confidence*100)}%</text>
      </g>;
    })}
  </svg>;
}
