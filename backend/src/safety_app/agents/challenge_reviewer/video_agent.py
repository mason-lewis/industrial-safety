from pathlib import Path

from safety_app.agents.shared.factory import build
from safety_app.domain.video_analysis import VideoVerification


def create(model, deps):
    return build('challenge_reviewer', model, deps, VideoVerification,
                 Path(__file__).with_name('video_prompt.md'), [], mode='recorded_video_verifier')
