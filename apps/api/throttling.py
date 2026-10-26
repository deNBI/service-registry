"""
API throttles
=============
DRF's throttles identify an anonymous client from the whole ``X-Forwarded-For``
value (or ``REMOTE_ADDR``). Behind the reverse proxy that value is a chain, so
one client could end up with several counts, and ``REMOTE_ADDR`` is the proxy's
own address.

These subclasses identify the client with ``get_ratelimit_ip``, the same
function that keys the web-form rate limits (``RATELIMIT_IP_META_KEY``), and
group addresses into networks the way django-ratelimit does for ``key="ip"``
(IPv4 per address, IPv6 per /64 by default; ``RATELIMIT_IPV4_MASK`` and
``RATELIMIT_IPV6_MASK``), so the API and the web forms count a client
identically. Authenticated requests are still counted per user / key, exactly
as in DRF.

``REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]`` names these classes. A view that
sets its own ``throttle_classes`` must use them too.
"""

import ipaddress

from django.conf import settings
from rest_framework import throttling

from apps.submissions.http_utils import get_ratelimit_ip


class ClientIPMixin:
    """Identify the client by the shared real-client-IP helper and network mask."""

    def get_ident(self, request):
        ip = ipaddress.ip_address(get_ratelimit_ip(request))
        if ip.version == 6:
            mask = getattr(settings, "RATELIMIT_IPV6_MASK", 64)
        else:
            mask = getattr(settings, "RATELIMIT_IPV4_MASK", 32)
        return str(ipaddress.ip_network(f"{ip}/{mask}", strict=False).network_address)


class AnonRateThrottle(ClientIPMixin, throttling.AnonRateThrottle):
    pass


class UserRateThrottle(ClientIPMixin, throttling.UserRateThrottle):
    pass


class ScopedRateThrottle(ClientIPMixin, throttling.ScopedRateThrottle):
    pass
