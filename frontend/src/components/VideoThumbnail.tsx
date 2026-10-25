import {useEffect,useState} from 'react';
import Icon from './ReviewIcon';

export default function VideoThumbnail({sourceId}: {sourceId: string}) {
  const [failed,setFailed] = useState(false);
  useEffect(()=>setFailed(false),[sourceId]);
  return failed ? <span className="library-video-icon"><Icon name="video"/></span>
    : <img className="library-thumbnail" src={`/api/preview/${encodeURIComponent(sourceId)}/thumbnail`}
        alt="" loading="lazy" decoding="async" onError={()=>setFailed(true)}/>;
}
