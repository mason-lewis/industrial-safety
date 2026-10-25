"""Versioned token costing. Reservation allowances are never invoice charges."""
import math
from decimal import Decimal


def token_cost(usage, price, provider_details=None, *, single_cache_write_ttl=None):
    details = getattr(usage, 'details', {}) or {}
    provider = provider_details or {}
    total = getattr(usage, 'input_tokens', None)
    output = getattr(usage, 'output_tokens', None)
    cached = getattr(usage, 'cache_read_tokens', 0) or 0
    written = getattr(usage, 'cache_write_tokens', 0) or 0
    tier = provider.get('service_tier', 'standard')
    writes = details.get('cache_write_tokens_details', provider.get('cache_write_tokens_details', {}))
    reported_split = 'cache_write_tokens_details' in details or 'cache_write_tokens_details' in provider
    if not isinstance(writes, dict):
        writes = {}
    short = writes.get('ttl_30m_tokens', details.get('cache_write_30m_tokens', 0)) or 0
    long = writes.get('ttl_12h_tokens', details.get('cache_write_12h_tokens', 0)) or 0
    breakdown_basis = 'provider_reported'
    has_breakdown = reported_split or bool({'ttl_30m_tokens','ttl_12h_tokens'} & writes.keys() or
                         {'cache_write_30m_tokens','cache_write_12h_tokens'} & details.keys())
    # PydanticAI 2.54 retains total writes but drops Responses' nested TTL split.
    # Only a caller that knows every breakpoint uses one lifetime may supply it.
    # Never replace a supplied (possibly inconsistent) provider breakdown.
    if written and not has_breakdown and single_cache_write_ttl in ('30m','12h'):
        short, long = (written, 0) if single_cache_write_ttl == '30m' else (0, written)
        breakdown_basis = 'uniform_request_ttl:' + single_cache_write_ttl
    result = dict(input_tokens=total, output_tokens=output, cached_input_tokens=cached,
                  cache_write_tokens=written, cache_write_30m_tokens=short,
                  cache_write_12h_tokens=long, uncached_input_tokens=None,
                  pricing_version=price.get('version'), service_tier=tier,
                  usage_cost_usd=None, cost_basis='provider_reported_tokens', billed_cost_usd=None)
    result['cache_write_breakdown_basis'] = breakdown_basis if written else 'no_writes'
    counts = (total, output, cached, written, short, long)
    if not usage.has_values() or any(type(v) is not int or v < 0 for v in counts):
        result['cost_unknown_reason'] = 'Missing or invalid provider usage'
        return result
    if cached + written > total or short + long != written or tier not in ('standard', 'default', 'auto'):
        result['cost_unknown_reason'] = 'Unresolved cache-write breakdown or service tier'
        return result
    uncached = total - cached - written
    result['uncached_input_tokens'] = uncached
    buckets = [(uncached, 'input_per_million'), (output, 'output_per_million'),
               (cached, 'cached_input_per_million'), (short, 'cache_write_30m_per_million'),
               (long, 'cache_write_12h_per_million')]
    cost = Decimal(0)
    for count, key in buckets:
        if not count:
            continue
        rate = price.get(key)
        if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
            result['cost_unknown_reason'] = 'Missing token rate: ' + key
            return result
        multiplier=(Decimal('1.5') if key=='output_per_million' else Decimal(2)) if total>272000 else Decimal(1)
        cost += Decimal(count) * Decimal(str(rate)) * multiplier / Decimal(1000000)
    result['usage_cost_usd'] = float(cost)
    return result
