"""Install the official pretrained person detector weights. Never train or overwrite a model."""
import hashlib
import os
from pathlib import Path
import tempfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def main():
    from rfdetr.assets.model_weights import ModelWeights
    asset=ModelWeights.from_filename('rf-detr-small.pth')
    if not asset or not asset.md5_hash or not asset.url.startswith('https://'):
        raise SystemExit('Pinned pretrained model metadata is unavailable')
    target=ROOT/'data/artifacts/models'/asset.filename
    def valid(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'md5').hexdigest()==asset.md5_hash
    if target.exists():
        if not valid(target):raise SystemExit('Existing model differs from the official checkpoint; preserved unchanged. Move it aside explicitly before preparing stock weights.')
        print('Standard person detector weights are ready. No training or AI API calls.')
        return
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=target.parent,prefix='.stock-people-');os.close(fd)
    temporary=Path(name)
    try:
        print('Downloading the official pretrained person detector (one-time local setup).',flush=True)
        urllib.request.urlretrieve(asset.url,temporary)
        if not valid(temporary):raise SystemExit('Checkpoint download failed integrity verification')
        # Hard-link publication refuses to replace a file created concurrently.
        os.link(temporary,target)
    finally:temporary.unlink(missing_ok=True)
    print('Standard person detector ready. No training or AI API calls.')


if __name__=='__main__':main()
