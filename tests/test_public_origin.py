import orjson
import pytest

from twicc.core.services.public_origin import normalize_public_origin, repair_legacy_public_origin, usable_public_origin


CASES = orjson.loads((__import__("pathlib").Path(__file__).parent / "fixtures/public_origin_cases.json").read_bytes())


def test_normalize_public_origin_matches_shared_contract():
    for case in CASES["cases"]:
        result = normalize_public_origin(case["input"])
        assert (result.value, result.error) == (case["value"], case["error"]), case["name"]


def test_repair_legacy_public_origin_matches_shared_contract():
    for case in CASES["repair_cases"]:
        result = repair_legacy_public_origin(case["input"])
        assert (result.value, result.error) == (case["value"], case["error"]), case["name"]


def test_normalized_origin_exposes_metadata():
    result = normalize_public_origin("HTTPS://Example.COM:8443/")
    assert result.scheme == "https"
    assert result.hostname == "example.com"
    assert result.port == 8443


def test_usable_public_origin_fails_closed_for_invalid_value():
    assert usable_public_origin("https://valid.example.com/") == "https://valid.example.com"
    assert usable_public_origin("ftp://unsafe.example.com") == ""


def test_settings_read_only_migrates_deployed_public_origins(tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(orjson.dumps({
        "publicBaseUrl": "public.example.com",
        "shareBaseUrl": "HTTPS://Share.Example.COM/",
        "peerBaseUrl": "peer.example.com",
    }))
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        settings = ss.read_synced_settings()
        assert settings["publicBaseUrl"] == "https://public.example.com"
        assert settings["shareBaseUrl"] == "https://share.example.com"
        assert settings["peerBaseUrl"] == "peer.example.com"
        persisted = orjson.loads(path.read_bytes())
        assert persisted["publicBaseUrl"] == "https://public.example.com"
        assert persisted["shareBaseUrl"] == "https://share.example.com"
        assert persisted["peerBaseUrl"] == "peer.example.com"
    finally:
        ss._cache.clear()


def test_public_origin_migration_is_idempotent():
    import twicc.synced_settings as ss

    settings = {
        "publicBaseUrl": "public.example.com",
        "shareBaseUrl": "https://share.example.com/base",
    }
    assert ss._migrate_legacy_settings(settings) is True
    migrated = settings.copy()
    assert ss._migrate_legacy_settings(settings) is False
    assert settings == migrated


@pytest.mark.parametrize("key", ["publicBaseUrl", "shareBaseUrl"])
def test_settings_read_retains_unsafe_public_origin(key, tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(orjson.dumps({key: "ftp://unsafe.example.com"}))
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        assert ss.read_synced_settings()[key] == "ftp://unsafe.example.com"
        assert orjson.loads(path.read_bytes())[key] == "ftp://unsafe.example.com"
    finally:
        ss._cache.clear()


def test_settings_read_retains_non_string_public_origin(tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(orjson.dumps({"shareBaseUrl": 42}))
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        assert ss.read_synced_settings()["shareBaseUrl"] == 42
        assert orjson.loads(path.read_bytes())["shareBaseUrl"] == 42
    finally:
        ss._cache.clear()


def test_canonicalize_hostname_strict_contract():
    from twicc.core.services.public_origin import canonicalize_hostname

    # localhost is case-insensitive with a fixed canonical value.
    assert canonicalize_hostname("LocalHost", bracketed=False) == ("localhost", False)
    # Canonical IPv4 only.
    assert canonicalize_hostname("192.168.1.42", bracketed=False) == ("192.168.1.42", False)
    assert canonicalize_hostname("192.168.001.1", bracketed=False).hostname is None
    assert canonicalize_hostname("1.2.3", bracketed=False).hostname is None
    # Bracketed IPv6 canonicalizes to lower-case compressed RFC 5952.
    assert canonicalize_hostname("0:0:0:0:0:0:0:1", bracketed=True) == ("::1", True)
    assert canonicalize_hostname("2001:DB8::1", bracketed=True) == ("2001:db8::1", True)
    assert canonicalize_hostname("::ffff:1.2.3.4", bracketed=True) == ("::ffff:1.2.3.4", True)
    assert canonicalize_hostname("1.2.3.4", bracketed=True).hostname is None
    assert canonicalize_hostname("fe80::1%eth0", bracketed=True).hostname is None
    assert canonicalize_hostname("fe80::1%25eth0", bracketed=True).hostname is None
    # DNS grammar: LDH labels, alphanumeric edges, 1-63 chars per label.
    assert canonicalize_hostname("Example.COM", bracketed=False) == ("example.com", False)
    assert canonicalize_hostname("devbox", bracketed=False) == ("devbox", False)
    assert canonicalize_hostname("a..example", bracketed=False).hostname is None
    assert canonicalize_hostname("example.com.", bracketed=False).hostname is None
    assert canonicalize_hostname("-a.example", bracketed=False).hostname is None
    assert canonicalize_hostname("a-.example", bracketed=False).hostname is None
    assert canonicalize_hostname("my_host.example", bracketed=False).hostname is None
    assert canonicalize_hostname("a" * 63 + ".example", bracketed=False).hostname == "a" * 63 + ".example"
    assert canonicalize_hostname("a" * 64 + ".example", bracketed=False).hostname is None
    long_253 = ".".join(["a" * 63] * 3 + ["a" * 61])
    assert canonicalize_hostname(long_253, bracketed=False).hostname == long_253
    assert canonicalize_hostname(long_253 + "a", bracketed=False).hostname is None
    # ASCII only: Unicode and percent escapes are invalid, never converted.
    assert canonicalize_hostname("exämple.com", bracketed=False).hostname is None
    assert canonicalize_hostname("%65xample.com", bracketed=False).hostname is None
    # A-labels: valid IDNA2008 round-trips survive, malformed ones are invalid.
    assert canonicalize_hostname("XN--FA-HIA.de", bracketed=False) == ("xn--fa-hia.de", False)
    assert canonicalize_hostname("xn--a-ecp.example", bracketed=False).hostname is None
    assert canonicalize_hostname("xn--e28h.example", bracketed=False).hostname is None


def test_normalize_public_origin_strict_hostnames():
    assert normalize_public_origin("https://[0:0:0:0:0:0:0:1]:8443").value == "https://[::1]:8443"
    assert normalize_public_origin("HTTPS://XN--FA-HIA.DE").value == "https://xn--fa-hia.de"
    assert normalize_public_origin("https://xn--.example").error == "host"
    assert normalize_public_origin("https://exämple.com").error == "host"
    assert normalize_public_origin("https://%65xample.com").error == "host"
    assert normalize_public_origin("https://[1.2.3.4]").error == "host"
    assert normalize_public_origin("https://example.com:").error == "port"
    assert normalize_public_origin("https://exa\tmple.com").error == "host"
    assert normalize_public_origin("https://example.com:8\t0").error == "port"
    assert normalize_public_origin("https://example.com?").error == "query"
    assert normalize_public_origin("https://example.com#").error == "fragment"
    assert normalize_public_origin("https://example.com?\t#").error == "query"
    assert normalize_public_origin("https://example.com#x?y").error == "fragment"


def test_normalized_origin_exposes_routing_authority():
    assert normalize_public_origin("https://Example.com:443").authority == "example.com"
    assert normalize_public_origin("https://Example.com:8443").authority == "example.com:8443"
    assert normalize_public_origin("http://example.com").authority == "example.com"
    assert normalize_public_origin("https://[0:0:0:0:0:0:0:1]:8443").authority == "[::1]:8443"
    assert normalize_public_origin("").authority is None
    assert normalize_public_origin("ftp://x.example").authority is None


def test_classify_peer_external():
    from twicc.core.services.public_origin import classify_peer_external

    def parse(value):
        return normalize_public_origin(value)

    assert classify_peer_external(parse("https://x.example"), parse("https://x.example")) == "shared"
    assert classify_peer_external(parse("http://x.example"), parse("https://x.example")) == "ambiguous"
    assert classify_peer_external(parse("https://x.example:8443"), parse("https://x.example")) == "dedicated"
    assert classify_peer_external(parse("https://x.example:8443"), parse("https://x.example:7443")) == "dedicated"
    assert classify_peer_external(parse("https://x.example"), parse("")) == "dedicated"
    assert classify_peer_external(parse(""), parse("https://x.example")) is None
    assert classify_peer_external(parse("ftp://x.example"), parse("https://x.example")) is None
    # IPv6 spellings normalize before comparison: expanded peer == compressed external.
    assert classify_peer_external(parse("https://[0:0:0:0:0:0:0:1]:8443"), parse("https://[::1]:8443")) == "shared"


def test_validate_origin_settings():
    from twicc.core.services.public_origin import validate_origin_settings

    all_fields = {"publicBaseUrl", "shareBaseUrl", "peerBaseUrl"}
    assert validate_origin_settings("", "", "", changed_fields=all_fields) == ()
    assert validate_origin_settings(
        "https://app.example", "https://share.example", "https://peer.example", changed_fields=all_fields,
    ) == ()
    # Structural errors name invalid changed fields only.
    errors = validate_origin_settings(
        "ftp://app.example", "https://share.example", 42, changed_fields=all_fields,
    )
    assert [(e.field, e.code) for e in errors] == [
        ("publicBaseUrl", "invalid_origin_scheme"),
        ("peerBaseUrl", "invalid_origin_type"),
    ]
    # An unchanged invalid field does not block another field's repair.
    assert validate_origin_settings(
        "ftp://app.example", "https://share.example", "https://peer.example",
        changed_fields={"peerBaseUrl"},
    ) == ()
    # Share/External hostname conflict names both fields; a port does not help.
    errors = validate_origin_settings(
        "https://x.example", "https://x.example:9443", "", changed_fields={"shareBaseUrl"},
    )
    assert [(e.field, e.code) for e in errors] == [
        ("shareBaseUrl", "origin_conflict_share_external_hostname"),
        ("publicBaseUrl", "origin_conflict_share_external_hostname"),
    ]
    # Share/Peer hostname conflict.
    errors = validate_origin_settings(
        "", "https://x.example", "http://x.example:8443", changed_fields={"peerBaseUrl"},
    )
    assert [(e.field, e.code) for e in errors] == [
        ("shareBaseUrl", "origin_conflict_share_peer_hostname"),
        ("peerBaseUrl", "origin_conflict_share_peer_hostname"),
    ]
    # Ambiguous Peer/External: same authority, different origins.
    errors = validate_origin_settings(
        "https://x.example", "", "http://x.example", changed_fields={"peerBaseUrl"},
    )
    assert [(e.field, e.code) for e in errors] == [
        ("peerBaseUrl", "origin_conflict_ambiguous_authority"),
        ("publicBaseUrl", "origin_conflict_ambiguous_authority"),
    ]
    # Shared Peer/External is valid.
    assert validate_origin_settings(
        "https://x.example", "", "https://x.example", changed_fields={"peerBaseUrl"},
    ) == ()
    # A structural error does not hide a conflict between other changed fields.
    errors = validate_origin_settings(
        "ftp://app.example", "https://x.example", "http://x.example:8443",
        changed_fields=all_fields,
    )
    assert [(e.field, e.code) for e in errors] == [
        ("publicBaseUrl", "invalid_origin_scheme"),
        ("shareBaseUrl", "origin_conflict_share_peer_hostname"),
        ("peerBaseUrl", "origin_conflict_share_peer_hostname"),
    ]


def test_authority_cases_match_backend_contract():
    for case in CASES["authority_cases"]:
        result = normalize_public_origin(case["input"])
        assert (result.hostname, result.authority) == (case["hostname"], case["authority"]), case["name"]


def test_cross_cases_match_backend_contract():
    from twicc.core.services.public_origin import classify_peer_external, validate_origin_settings

    for case in CASES["cross_cases"]:
        errors = validate_origin_settings(
            case["publicBaseUrl"], case["shareBaseUrl"], case["peerBaseUrl"],
            changed_fields=set(case["changed_fields"]),
        )
        assert [{"field": error.field, "code": error.code} for error in errors] == case["errors"], case["name"]
        if case["peer_routing"] is not None:
            routing = classify_peer_external(
                normalize_public_origin(case["peerBaseUrl"]),
                normalize_public_origin(case["publicBaseUrl"]),
            )
            assert routing == case["peer_routing"], case["name"]


def test_every_frontend_rejection_is_also_a_backend_rejection():
    backend = {case["input"]: case for case in CASES["cases"]}
    for case in CASES["frontend_input_cases"]:
        if case["error"] is None:
            continue
        assert case["input"] in backend, case["name"]
        assert backend[case["input"]]["error"] is not None, case["name"]


@pytest.mark.parametrize(
    "public_value,share_value,peer_value,changed_fields",
    [
        ("", "ftp://peer.example", "https://peer.example", {"peerBaseUrl"}),
        ("ftp://share.example", "https://share.example", "", {"shareBaseUrl"}),
        ("ftp://peer.example", "", "http://peer.example", {"peerBaseUrl"}),
    ],
)
def test_invalid_origin_result_metadata_never_becomes_a_relationship_operand(
    monkeypatch, public_value, share_value, peer_value, changed_fields,
):
    from twicc.core.services import public_origin

    original = public_origin.normalize_public_origin

    def normalize(value):
        if isinstance(value, str) and value.startswith("ftp://"):
            hostname = value.removeprefix("ftp://")
            return public_origin.PublicOriginResult(
                None, "scheme", "ftp", hostname, None, hostname,
            )
        return original(value)

    monkeypatch.setattr(public_origin, "normalize_public_origin", normalize)
    assert public_origin.validate_origin_settings(
        public_value, share_value, peer_value, changed_fields=changed_fields,
    ) == ()


def test_settings_read_preserves_invalid_peer_base_url(tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(orjson.dumps({"peerBaseUrl": "ftp://peer.example/forbidden"}))
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        assert ss.read_synced_settings()["peerBaseUrl"] == "ftp://peer.example/forbidden"
        assert orjson.loads(path.read_bytes())["peerBaseUrl"] == "ftp://peer.example/forbidden"
    finally:
        ss._cache.clear()


# ── One-way property: the JS subset never rejects what Python accepts ────────
# The two parsers are checked against the SAME frozen list of adversarial
# inputs: Python must still accept every one of them (below), and the frontend
# must not reject any (frontend/src/utils/publicOrigin.test.js). No generator
# runs at test time and no cross-language runner is needed — the list IS the
# shared observation.
#
# To regenerate after an intentional Python change, run
# ``test_one_way_fixture_is_the_current_accepted_set`` and follow its failure:
# the expected list it builds is the new fixture content.

_ONE_WAY_SCHEMES = ["", "http://", "https://", "HTTP://", "HtTpS://", "hTTps://"]

_ONE_WAY_HOSTS = [
    "localhost", "LOCALHOST", "LocalHost",
    "127.0.0.1", "127.000.000.001", "0.0.0.0", "192.168.1.42", "10.0.0.1", "255.255.255.255",
    "[::1]", "[::]", "[0:0:0:0:0:0:0:1]", "[2001:db8::1]", "[2001:DB8::1]",
    "[::ffff:1.2.3.4]", "[fe80::1]", "[fd00:ec2::254]",
    "example.com", "EXAMPLE.COM", "Example.Com",
    "sub.example.com", "a.b.c.d.e.example.com",
    "xn--fa-hia.de", "XN--FA-HIA.DE", "xn--e28h.example",
    "twicc.local", "twicc.test", "twicc.localhost", "TWICC.LOCAL",
    "devbox", "single", "x", "0", "9",
    "a-b.example.com", "a--b.example.com", "1a.example.com", "a1.example.com",
    "9lives.example", "123.example",
    "a" * 63 + ".example.com",
    ".".join(["a" * 49] * 5),                       # 253 characters exactly
    "xn--fa-hia.xn--fa-hia.de",
    "under_score.example.com", "-lead.example.com", "trail-.example.com",
    "a..example.com", "example.com.", ".example.com",
    "exa mple.com", "exämple.com", "%65xample.com", "[xyz]", "[]", "",
    "user@example.com", "example.com:extra",
]

_ONE_WAY_PORTS = [
    "", ":0", ":00", ":80", ":443", ":8443", ":3501", ":65535", ":65536", ":00080",
    ":000000000080", ":", ":bad", ":-1", ":99999",
]

_ONE_WAY_SUFFIXES = ["", "/", "//", "/base", "?x=1", "#frag", "?", "#", "/?x=1", "/#f"]

_ONE_WAY_WRAPS = ["{}", " {} ", "\t{}", "{}\r\n", "\n\t {} \r ", "\x0b{}\x0c"]

# One host per family, for the decoration sweeps.
_ONE_WAY_REPRESENTATIVES = [
    "example.com",          # plain DNS, https default
    "EXAMPLE.COM",          # case folding
    "localhost",            # local name, http default
    "192.168.1.42",         # IPv4
    "[2001:db8::1]",        # IPv6, compressed
    "[0:0:0:0:0:0:0:1]",    # IPv6, expanded then compressed
    "xn--fa-hia.de",        # IDNA a-label
    "twicc.local",          # local suffix
    "devbox",               # dotless
]


def _one_way_accepted(raw):
    result = normalize_public_origin(raw)
    return result.error is None and bool(result.value)


def build_one_way_accepted_inputs() -> list[str]:
    """The frozen list: adversarial inputs Python accepts.

    The full product is ~300k inputs / ~28k accepted, far too many to freeze.
    Three bounded coverage sets replace it, in this order:

      A. every accepted (host, port) pair, in its plainest spelling;
      B. every PAIR of decoration values, on two structurally different hosts;
      C. one decoration at a time, on every representative host.
    """
    import itertools

    kept: list[str] = []
    seen: set[str] = set()

    def keep(raw: str) -> None:
        if raw not in seen and _one_way_accepted(raw):
            seen.add(raw)
            kept.append(raw)

    for host, port in itertools.product(_ONE_WAY_HOSTS, _ONE_WAY_PORTS):
        for scheme in ("", "https://", "http://"):
            before = len(kept)
            keep(f"{scheme}{host}{port}")
            if len(kept) != before:
                break

    dimensions = {
        "scheme": _ONE_WAY_SCHEMES,
        "port": _ONE_WAY_PORTS,
        "suffix": _ONE_WAY_SUFFIXES,
        "wrap": _ONE_WAY_WRAPS,
    }
    base = {"scheme": "", "port": "", "suffix": "", "wrap": "{}"}

    def spelling(host: str, spec: dict) -> str:
        return spec["wrap"].format(f"{spec['scheme']}{host}{spec['port']}{spec['suffix']}")

    for host in ("example.com", "[2001:db8::1]"):
        for left, right in itertools.combinations(dimensions, 2):
            for left_value, right_value in itertools.product(dimensions[left], dimensions[right]):
                keep(spelling(host, {**base, left: left_value, right: right_value}))

    for host in _ONE_WAY_REPRESENTATIVES:
        for name, values in dimensions.items():
            for value in values:
                keep(spelling(host, {**base, name: value}))

    return kept


def test_one_way_fixture_is_the_current_accepted_set():
    """The frozen list must stay exactly what Python accepts today.

    A failure here is not a defect on its own: it means the backend contract
    moved. Replace ``one_way_accepted_inputs`` in the fixture with the list
    this builder returns, then re-run the frontend suite — that is where the
    one-way property is actually enforced.
    """
    assert CASES["one_way_accepted_inputs"] == build_one_way_accepted_inputs()


def test_one_way_fixture_covers_every_host_family():
    inputs = CASES["one_way_accepted_inputs"]
    assert len(inputs) > 500
    for host in _ONE_WAY_REPRESENTATIVES:
        assert any(host.lower() in raw.lower() for raw in inputs), host


def test_one_way_fixture_inputs_are_all_accepted():
    for raw in CASES["one_way_accepted_inputs"]:
        result = normalize_public_origin(raw)
        assert result.error is None and result.value, repr(raw)


def test_migration_drops_fixed_theme_and_accent_keys():
    import twicc.synced_settings as ss

    settings = {"waTheme": "awesome", "waBrand": "red", "autoUnpinOnArchive": False}
    assert ss._migrate_legacy_settings(settings) is True
    assert settings == {"autoUnpinOnArchive": False}
