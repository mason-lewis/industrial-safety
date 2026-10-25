"""Bounded review transport; trace metadata never includes headers, bodies or credentials."""
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
import ssl
import httpx
from openai import AsyncOpenAI
from safety_app.domain.models import uid


@dataclass
class RequestTrace:
    client_request_id: str = field(default_factory=uid)
    phase: str = 'not_started'
    headers_started: bool = False
    transport_observed: bool = False
    connect_failed: bool = False
    http_request_id: str | None = None
    status_code: int | None = None
    body_complete: bool = False
    failed_phase: str | None = None

    async def event(self, name, info):
        self.transport_observed = True
        # httpcore event names are constants; info can contain private request data.
        self.phase = name
        if 'send_request_headers.started' in name:
            self.headers_started = True
        if name.endswith(('connect_tcp.failed', 'start_tls.failed')):
            self.connect_failed = True
        if name.endswith('send_request_body.complete'):
            self.body_complete = True
        # httpcore always ends with response_closed; keep the step that actually failed.
        if name.endswith('.failed') and self.failed_phase is None:
            self.failed_phase = name

    def unsent_basis(self, error=None):
        """Why the provider cannot have received a complete request, or None if ambiguous."""
        if not self.transport_observed or self.status_code is not None:
            return None
        if self.connect_failed and not self.headers_started:
            return 'connection_failed_before_request'
        if not self.body_complete and self.failed_phase and 'send_request' in self.failed_phase:
            return 'request_write_failed_before_body_complete'
        # A received bad_record_mac alert means the provider's TLS layer rejected a
        # record we sent and terminated. The only records sent since the previous
        # complete response belong to this request, so it never fully arrived.
        if peer_rejected_record(error):
            return 'provider_rejected_corrupted_tls_record'
        return None

    @property
    def proven_unsent(self):
        return self.unsent_basis() is not None

    def diagnostics(self, error=None):
        original = error
        causes = []
        tls = []
        seen = set()
        while error is not None and id(error) not in seen and len(causes) < 8:
            seen.add(id(error)); causes.append(type(error).__name__)
            # OpenSSL's symbolic reason/library are useful without disclosing
            # exception messages, URLs, request bodies or credentials.
            if isinstance(error, ssl.SSLError):
                tls.append({k:v for k in ('reason','library')
                            if isinstance((v:=getattr(error,k,None)),str)
                            and re.fullmatch(r'[A-Z0-9_]{1,100}',v)})
            error = error.__cause__ or error.__context__
        basis = self.unsent_basis(original)
        return {'client_request_id': self.client_request_id, 'transport_phase': self.phase,
                'failed_phase': self.failed_phase, 'request_body_complete': self.body_complete,
                'http_request_id': self.http_request_id, 'http_status': self.status_code,
                'proven_not_dispatched': basis is not None, 'unsent_basis': basis,
                'exception_types': causes, 'tls_errors': tls}


def peer_rejected_record(error):
    """True only for a bad_record_mac alert received from the peer, not a local decrypt failure."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ssl.SSLError) and getattr(error, 'reason', None) == 'SSLV3_ALERT_BAD_RECORD_MAC':
            return True
        error = error.__cause__ or error.__context__
    return False


current_trace: ContextVar[RequestTrace | None] = ContextVar('review_request_trace', default=None)


async def request_hook(request):
    trace = current_trace.get()
    # Non-generation requests (metadata/counting) also receive distinct IDs.
    request.headers['X-Client-Request-Id'] = trace.client_request_id if trace else uid()
    if trace:
        trace.phase = 'request_prepared'
        request.extensions['trace'] = trace.event


async def response_hook(response):
    trace = current_trace.get()
    if trace:
        trace.http_request_id = response.headers.get('x-request-id')
        trace.status_code = response.status_code


def review_client(deadline=60):
    timeout = httpx.Timeout(connect=min(5, deadline), read=min(45, deadline),
                            write=min(15, deadline), pool=min(5, deadline))
    http = httpx.AsyncClient(timeout=timeout, verify=True, follow_redirects=False,
                             event_hooks={'request': [request_hook], 'response': [response_hook]})
    return AsyncOpenAI(max_retries=0, timeout=timeout, http_client=http)


def connection_message(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if type(error).__name__ in {'APIConnectionError', 'APITimeoutError', 'ConnectError', 'ReadTimeout', 'WriteTimeout'}:
            return ('The connection to OpenAI was interrupted. Completed evidence is saved. '
                    'Check the connection and charge status before submitting again; no automatic retry was made.')
        error = error.__cause__ or error.__context__
    return None


async def check_review_connection(model='gpt-6-luna'):
    async with review_client(10) as client:
        result = await client.models.retrieve(model)
        if result.id != model:
            raise ValueError('Model access probe returned a different model')
        return {'model': result.id, 'http_request_id': result._request_id,
                'generation_dispatched': False, 'status': 'reachable'}
