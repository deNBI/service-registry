"""API rate limits identify clients the same way as the web-form rate limits.

The test settings switch DRF throttling off (the locmem cache would otherwise
be shared between tests). These tests switch it back on with the throttle
classes named in ``config/settings.py`` and small rates, and give every test a
fresh cache and a frozen throttle clock.

Behind the production reverse proxy ``REMOTE_ADDR`` is the proxy's own address,
``X-Real-IP`` holds the client address and ``X-Forwarded-For`` holds whatever
the client sent followed by the client address. A request is counted against
``apps.submissions.http_utils.get_ratelimit_ip`` and grouped into networks
exactly like the web forms.
"""

import ipaddress

import pytest
from django.core.cache import cache
from django.utils.module_loading import import_string
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.api.models import AdminAPIKey
from tests.factories import APIKeyFactory, ServiceSubmissionFactory

PROXY_IP = "172.18.0.2"
EDAM_LIST = "/api/v1/edam/"
NOW = {"t": 1_800_000_000.0}


def _configured_throttle_classes():
    """The throttle classes of the real (non-test) REST_FRAMEWORK settings."""
    from config import settings as project_settings

    return [
        import_string(path)
        for path in project_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]
    ]


@pytest.fixture
def set_rates(monkeypatch):
    """Enable the configured throttles with the given rates."""

    def _set(anon="3/min", user="4/min", **scopes):
        monkeypatch.setattr(APIView, "throttle_classes", _configured_throttle_classes())
        monkeypatch.setattr(
            SimpleRateThrottle,
            "THROTTLE_RATES",
            {"anon": anon, "user": user, **scopes},
        )

    return _set


@pytest.fixture(autouse=True)
def _isolated_throttle_state(monkeypatch):
    """Fresh cache and a frozen, adjustable throttle clock for every test."""
    NOW["t"] = 1_800_000_000.0
    monkeypatch.setattr(SimpleRateThrottle, "timer", staticmethod(lambda: NOW["t"]))
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def throttled(set_rates):
    set_rates()


def _proxied(client_ip, sent_by_client=None, path=EDAM_LIST, api=None):
    """GET as nginx forwards it: X-Real-IP plus an appended X-Forwarded-For."""
    forwarded = client_ip if not sent_by_client else f"{sent_by_client}, {client_ip}"
    return (api or APIClient()).get(
        path,
        REMOTE_ADDR=PROXY_IP,
        HTTP_X_REAL_IP=client_ip,
        HTTP_X_FORWARDED_FOR=forwarded,
    )


def _hit_until_limited(make_request, limit):
    """Send `limit` requests, all of which must pass; return the next response."""
    for i in range(limit):
        response = make_request(i)
        assert response.status_code != 429, f"request {i + 1} limited too early"
    return make_request(limit)


# ---------------------------------------------------------------------------
# Client identity for anonymous requests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnonymousClientIdentity:
    def test_same_client_with_different_forwarded_values_shares_one_count(
        self, throttled
    ):
        response = _hit_until_limited(
            lambda i: _proxied("203.0.113.7", sent_by_client=f"198.51.100.{i + 1}"),
            limit=3,
        )
        assert response.status_code == 429

    def test_different_clients_have_separate_counts(self, throttled):
        for _ in range(3):
            assert _proxied("203.0.113.7").status_code == 200
        assert _proxied("203.0.113.7").status_code == 429
        assert _proxied("203.0.113.8").status_code == 200

    def test_without_proxy_headers_requests_count_by_remote_addr(self, throttled):
        def get(remote):
            return APIClient().get(EDAM_LIST, REMOTE_ADDR=remote)

        for _ in range(3):
            assert get("192.0.2.10").status_code == 200
        assert get("192.0.2.10").status_code == 429
        assert get("192.0.2.11").status_code == 200

    def test_forwarded_for_is_used_when_there_is_no_real_ip_header(self, throttled):
        def get(chain):
            return APIClient().get(
                EDAM_LIST, REMOTE_ADDR=PROXY_IP, HTTP_X_FORWARDED_FOR=chain
            )

        for hops in ("203.0.113.7", "203.0.113.7, 10.0.0.1", "203.0.113.7,10.0.0.2"):
            assert get(hops).status_code == 200
        assert get("203.0.113.7, 10.0.0.3").status_code == 429
        assert get("203.0.113.8, 10.0.0.1").status_code == 200

    @pytest.mark.parametrize(
        "headers",
        [
            {"HTTP_X_REAL_IP": "not-an-ip"},
            {"HTTP_X_REAL_IP": "198.51.100.29968"},
            {"HTTP_X_REAL_IP": "203.0.113.7:8080"},
            {"HTTP_X_REAL_IP": "fe80::1%eth0"},
            {"HTTP_X_REAL_IP": ""},
            {"HTTP_X_FORWARDED_FOR": "garbage, 198.51.100.1"},
            {"HTTP_X_FORWARDED_FOR": ""},
            {"HTTP_X_FORWARDED_FOR": ",,,"},
            {"HTTP_X_REAL_IP": "x", "HTTP_X_FORWARDED_FOR": "y"},
            {"HTTP_X_FORWARDED_FOR": "a" * 5000},
        ],
        ids=lambda h: "-".join(f"{k[5:]}={v[:12]!r}" for k, v in h.items()),
    )
    def test_malformed_headers_fall_back_to_remote_addr_without_errors(
        self, throttled, headers
    ):
        def get(extra):
            return APIClient().get(EDAM_LIST, REMOTE_ADDR="192.0.2.10", **extra)

        for _ in range(3):
            assert get(headers).status_code == 200
        # Same bucket as a request without any header: REMOTE_ADDR.
        assert get({}).status_code == 429
        assert get(headers).status_code == 429

    def test_no_valid_address_anywhere_shares_one_bucket_without_errors(
        self, throttled
    ):
        def get(extra):
            return APIClient().get(EDAM_LIST, REMOTE_ADDR="", **extra)

        for _ in range(3):
            assert get({"HTTP_X_FORWARDED_FOR": "junk"}).status_code == 200
        assert get({}).status_code == 429

    def test_ipv6_spellings_of_one_address_share_a_count(self, throttled):
        spellings = ["2001:db8::1", "2001:DB8:0::1", "2001:db8:0:0:0:0:0:1"]
        for ip in spellings:
            assert _proxied(ip).status_code == 200
        assert _proxied("2001:0db8::0001").status_code == 429

    def test_ipv6_addresses_in_one_network_share_a_count(self, throttled):
        """Like the web forms, IPv6 clients are counted per /64 network."""
        for i in range(3):
            assert _proxied(f"2001:db8::{i + 1}").status_code == 200
        assert _proxied("2001:db8::ffff:1").status_code == 429

    def test_ipv6_networks_are_counted_separately(self, throttled):
        for _ in range(3):
            assert _proxied("2001:db8::1").status_code == 200
        assert _proxied("2001:db8::1").status_code == 429
        assert _proxied("2001:db8:0:1::1").status_code == 200

    def test_ipv4_clients_are_counted_per_address(self, throttled):
        for _ in range(3):
            assert _proxied("203.0.113.7").status_code == 200
        assert _proxied("203.0.113.7").status_code == 429
        assert _proxied("203.0.113.8").status_code == 200

    @pytest.mark.parametrize(
        "anon,user",
        [("3/min", "100/min"), ("100/min", "3/min")],
        ids=["anon-scope-binds", "user-scope-binds"],
    )
    def test_each_throttle_scope_counts_per_client(self, set_rates, anon, user):
        """Anonymous requests are counted by both the anon and the user scope."""
        set_rates(anon=anon, user=user)
        response = _hit_until_limited(
            lambda i: _proxied("203.0.113.7", sent_by_client=f"198.51.100.{i + 1}"),
            limit=3,
        )
        assert response.status_code == 429
        assert _proxied("203.0.113.8").status_code == 200

    def test_limited_response_has_retry_after_and_error_envelope(self, throttled):
        response = _hit_until_limited(lambda i: _proxied("203.0.113.7"), limit=3)
        assert response.status_code == 429
        assert response["Retry-After"] == "60"
        body = response.json()
        assert "error" in body and "request_id" in body

    def test_requests_are_allowed_again_after_the_window(self, throttled):
        for _ in range(3):
            _proxied("203.0.113.7")
        assert _proxied("203.0.113.7").status_code == 429
        NOW["t"] += 61
        assert _proxied("203.0.113.7").status_code == 200


# ---------------------------------------------------------------------------
# Authenticated requests are counted per key, as before
# ---------------------------------------------------------------------------


def _admin_key(label="Example key"):
    import hashlib
    import secrets

    plaintext = secrets.token_urlsafe(48)
    key = AdminAPIKey.objects.create(
        label=label,
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        scope="full",
        is_active=True,
    )
    return key, plaintext


@pytest.mark.django_db
class TestAuthenticatedKeysUnchanged:
    def test_admin_key_is_counted_per_key_across_client_addresses(self, throttled):
        _, plaintext = _admin_key()
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
        response = _hit_until_limited(
            lambda i: _proxied(
                f"203.0.113.{i + 1}", path="/api/v1/categories/", api=api
            ),
            limit=4,
        )
        assert response.status_code == 429
        assert response["Retry-After"] == "60"

    def test_another_admin_key_has_its_own_count(self, throttled):
        _, first = _admin_key("first")
        _, second = _admin_key("second")
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"AdminKey {first}")
        for _ in range(4):
            assert (
                _proxied("203.0.113.7", path="/api/v1/pis/", api=api).status_code == 200
            )
        assert _proxied("203.0.113.7", path="/api/v1/pis/", api=api).status_code == 429
        other = APIClient()
        other.credentials(HTTP_AUTHORIZATION=f"AdminKey {second}")
        assert (
            _proxied("203.0.113.7", path="/api/v1/pis/", api=other).status_code == 200
        )

    def test_submission_key_is_counted_per_submission_across_client_addresses(
        self, throttled
    ):
        submission = ServiceSubmissionFactory(status="approved")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=submission)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        path = f"/api/v1/submissions/{submission.pk}/"
        response = _hit_until_limited(
            lambda i: _proxied(f"203.0.113.{i + 1}", path=path, api=api), limit=4
        )
        assert response.status_code == 429

    def test_authenticated_requests_do_not_use_up_the_anonymous_count(self, throttled):
        _, plaintext = _admin_key()
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
        for _ in range(4):
            assert (
                _proxied("203.0.113.7", path="/api/v1/pis/", api=api).status_code == 200
            )
        # Same client address, anonymous: its own count is untouched.
        for _ in range(3):
            assert _proxied("203.0.113.7").status_code == 200
        assert _proxied("203.0.113.7").status_code == 429

    def test_bucket_keys_name_the_key_id_not_an_address(self, throttled):
        key, plaintext = _admin_key()
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
        _proxied("203.0.113.7", path="/api/v1/pis/", api=api)
        assert cache.get(f"throttle_user_{key.pk}")
        assert cache.get("throttle_user_203.0.113.7") is None


# ---------------------------------------------------------------------------
# Every throttled endpoint uses the shared identification
# ---------------------------------------------------------------------------

# Endpoints an anonymous client can reach (all others answer 401 before the
# throttles run): public reference data, submission create, schema and docs.
ANONYMOUS_ENDPOINTS = [
    ("get", "/api/v1/edam/"),
    ("get", "/api/v1/edam/example-term/"),
    ("post", "/api/v1/submissions/"),
    ("get", "/api/v1/biotools/example-tool/"),
    ("get", "/api/schema/"),
    ("get", "/api/docs/"),
    ("get", "/api/redoc/"),
]

# Endpoints that need a key; the throttle counts the key, not the address.
ADMIN_KEY_ENDPOINTS = [
    "/api/v1/",
    "/api/v1/submissions/",
    "/api/v1/categories/",
    "/api/v1/service-centers/",
    "/api/v1/pis/",
    "/api/v1/biotools/",
]


@pytest.mark.django_db
class TestEveryThrottledEndpoint:
    @pytest.mark.parametrize("method,path", ANONYMOUS_ENDPOINTS)
    def test_anonymous_endpoint_counts_one_client_once(self, throttled, method, path):
        api = APIClient()

        def send(client_ip, sent):
            return getattr(api, method)(
                path,
                REMOTE_ADDR=PROXY_IP,
                HTTP_X_REAL_IP=client_ip,
                HTTP_X_FORWARDED_FOR=f"{sent}, {client_ip}",
            )

        response = _hit_until_limited(
            lambda i: send("203.0.113.7", f"198.51.100.{i + 1}"), limit=3
        )
        assert response.status_code == 429
        assert send("203.0.113.8", "198.51.100.1").status_code != 429

    @pytest.mark.parametrize("path", ADMIN_KEY_ENDPOINTS)
    def test_admin_key_endpoint_counts_the_key(self, throttled, path):
        _, plaintext = _admin_key()
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
        response = _hit_until_limited(
            lambda i: _proxied(f"203.0.113.{i + 1}", path=path, api=api), limit=4
        )
        assert response.status_code == 429

    @pytest.mark.parametrize("path", ADMIN_KEY_ENDPOINTS)
    def test_without_a_key_these_endpoints_answer_before_any_throttle(
        self, throttled, path
    ):
        for i in range(6):
            assert _proxied("203.0.113.7", path=path).status_code == 401


# ---------------------------------------------------------------------------
# Central configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_default_throttle_classes_are_the_project_classes(self):
        from apps.api import throttling

        configured = _configured_throttle_classes()
        assert configured == [throttling.AnonRateThrottle, throttling.UserRateThrottle]

    @pytest.mark.parametrize(
        "name", ["AnonRateThrottle", "UserRateThrottle", "ScopedRateThrottle"]
    )
    @pytest.mark.parametrize(
        "meta",
        [
            {"REMOTE_ADDR": "192.0.2.10"},
            {"REMOTE_ADDR": PROXY_IP, "HTTP_X_REAL_IP": "203.0.113.7"},
            {"REMOTE_ADDR": PROXY_IP, "HTTP_X_FORWARDED_FOR": "203.0.113.7, 10.0.0.1"},
            {"REMOTE_ADDR": PROXY_IP, "HTTP_X_REAL_IP": "junk"},
            {"REMOTE_ADDR": "", "HTTP_X_FORWARDED_FOR": "junk"},
            {"REMOTE_ADDR": "", "HTTP_X_REAL_IP": "2001:DB8::1"},
            {"REMOTE_ADDR": PROXY_IP, "HTTP_X_REAL_IP": "2001:db8::abcd:1234"},
        ],
    )
    def test_identity_equals_the_web_form_rate_limit_key(self, name, meta):
        """The key django-ratelimit computes for ``key="ip"`` on the web forms."""
        from django_ratelimit.core import _get_ip

        from apps.api import throttling

        request = APIRequestFactory().get("/", **meta)
        throttle = getattr(throttling, name).__new__(getattr(throttling, name))
        assert throttle.get_ident(request) == _get_ip(request)
        ipaddress.ip_address(throttle.get_ident(request))  # always parseable

    @pytest.mark.parametrize(
        "masks", [{"RATELIMIT_IPV6_MASK": 128}, {"RATELIMIT_IPV4_MASK": 24}]
    )
    def test_network_mask_settings_apply_like_the_web_forms(self, settings, masks):
        from django_ratelimit.core import _get_ip

        from apps.api.throttling import AnonRateThrottle

        for name, value in masks.items():
            setattr(settings, name, value)
        throttle = AnonRateThrottle.__new__(AnonRateThrottle)
        for ip in ("2001:db8::abcd:1234", "203.0.113.7"):
            request = APIRequestFactory().get(
                "/", REMOTE_ADDR=PROXY_IP, HTTP_X_REAL_IP=ip
            )
            assert throttle.get_ident(request) == _get_ip(request)

    def test_scoped_throttle_counts_per_client(self, set_rates):
        from rest_framework.response import Response
        from apps.api.throttling import ScopedRateThrottle

        class ExampleView(APIView):
            throttle_classes = [ScopedRateThrottle]
            throttle_scope = "example"
            authentication_classes = []
            permission_classes = []

            def get(self, request):
                return Response({})

        set_rates(example="2/min")
        factory = APIRequestFactory()

        def get(i):
            request = factory.get(
                "/",
                REMOTE_ADDR=PROXY_IP,
                HTTP_X_REAL_IP="203.0.113.7",
                HTTP_X_FORWARDED_FOR=f"198.51.100.{i + 1}, 203.0.113.7",
            )
            return ExampleView.as_view()(request)

        assert _hit_until_limited(get, limit=2).status_code == 429
