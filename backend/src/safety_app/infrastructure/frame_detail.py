"""Derive a crop from hash-verified original pixels; never from a downsized model image."""
import hashlib
import cv2
import numpy as np
from safety_app.agents.video_observer.video_tools import valid_box


def original_crop(media, frame_id, box, max_side=768):
    valid_box(box)
    ref=media.db.get('frame',frame_id)
    source=media.db.get('media',ref['media_id'])
    image=cv2.imdecode(np.frombuffer(media.image(frame_id),np.uint8),cv2.IMREAD_COLOR)
    if image is None:raise ValueError('Cannot decode retained frame')
    h,w=image.shape[:2];x,y,bw,bh=box
    left,top=int(x*w),int(y*h)
    right,bottom=min(w,max(left+1,round((x+bw)*w))),min(h,max(top+1,round((y+bh)*h)))
    crop=image[top:bottom,left:right];ch,cw=crop.shape[:2]
    scale=min(1,max_side/max(ch,cw))
    if scale<1:crop=cv2.resize(crop,(max(1,round(cw*scale)),max(1,round(ch*scale))))
    ok,raw=cv2.imencode('.jpg',crop,[cv2.IMWRITE_JPEG_QUALITY,85])
    if not ok:raise ValueError('Cannot encode original detail')
    raw=raw.tobytes();digest=hashlib.sha256(raw).hexdigest()
    path=media.root/'data/clips'/f'{digest}.jpg'
    if not path.exists():path.write_bytes(raw)
    metadata={'parent_frame_id':frame_id,'parent_hash':ref['content_hash'],'source_hash':source['content_hash'],
              'timestamp_ms':ref['timestamp_ms'],'box':box,'pixel_box':[left,top,right-left,bottom-top],
              'original_detail_pixels':[cw,ch],'content_hash':digest,
              'notice':'Crop of the same retained original frame; no additional time coverage.'}
    return metadata,raw
