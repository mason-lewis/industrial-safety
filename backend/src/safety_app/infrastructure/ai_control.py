"""Read the real server gates; never probe the provider or expose credentials."""
import os
from safety_app.domain.models import AiStatus


def ai_status(rt):
    stopped = rt.budgets.state('stopped') == '1'
    hosted = os.getenv('SAFETY_PAID_REVIEW') == '1'
    key = bool(os.getenv('OPENAI_API_KEY', '').strip())
    message = ('AI is off. Turn it on to analyze a video.' if stopped else
               'This server is in offline mode. Restart without --offline to use real AI.' if not hosted else
               'Add OPENAI_API_KEY to the server environment and restart to use real AI.' if not key else
               'Real AI is ready for the next video you open.')
    return AiStatus(enabled=hosted and key and not stopped, stopped=stopped,
                    hosted_allowed=hosted, key_configured=key, message=message)


def require_hosted(rt, mode):
    if mode == 'hosted':
        status = ai_status(rt)
        if not status.enabled:
            raise ValueError(status.message)
