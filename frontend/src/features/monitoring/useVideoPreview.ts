import {useCallback, useEffect, useRef, useState} from 'react';
import {request} from '../../api/client';
import type {RecordedSource} from '../../api/generated';

export function useVideoPreview() {
  const [sources, setSources] = useState<RecordedSource[]>([]);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState(false);
  const [revision, setRevision] = useState(0);
  const video = useRef<HTMLVideoElement>(null);
  const mounted = useRef(true);
  const uploadLock = useRef(false);
  const refresh = useCallback(async () => {
    const rows = await request<RecordedSource[]>('/video-analysis/sources');
    if (mounted.current) setSources(rows);
    return rows;
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh().catch(e => setError(String(e)));
    return () => { mounted.current = false; };
  }, [refresh]);

  function choose(id: string) {
    video.current?.pause(); setSelected(id); setError('');
    setRevision(value => value + 1);
  }
  async function upload(file: File) {
    if (uploadLock.current) return;
    uploadLock.current = true; setUploading(true); setError('');
    video.current?.pause();
    try {
      const before = await refresh();
      const response = await fetch(`/api/monitoring/uploads?name=${encodeURIComponent(file.name)}`, {
        method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file,
      });
      if (!response.ok) throw new Error((await response.json()).detail ?? 'Could not upload the video.');
      const rows = await refresh();
      const added = rows.find(row => !before.some(old => old.id === row.id));
      if (!added) throw new Error('Video saved. Select it from the library.');
      if (mounted.current) choose(added.id);
    } catch (e) { if (mounted.current) setError(String(e)); }
    finally { uploadLock.current = false; if (mounted.current) setUploading(false); }
  }
  return {sources, selected, choose, error, setError, uploading,
    revision, video, refresh, upload, inspect: () => setRevision(value => value + 1)};
}
