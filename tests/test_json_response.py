"""``JsonResponse`` serialized with orjson (``twicc.core.json_response``)."""

import enum
import json
import math
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import orjson
import pytest
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse, QueryDict
from django.utils.datastructures import MultiValueDict
from django.utils.functional import lazy
from django.utils.safestring import mark_safe

from twicc.core import json_response


class Point(NamedTuple):
    x: int
    y: int


class ItemsDict(dict):
    def items(self):
        return [("x", "overridden")]


class IterList(list):
    def __iter__(self):
        return iter(["overridden"])


class IterTuple(tuple):
    __slots__ = ()

    def __iter__(self):
        return iter(["overridden"])


class StrStr(str):
    def __str__(self):
        return "overridden"


class ReprInt(int):
    def __repr__(self):
        return "999"

    def __int__(self):
        return 999


class SubFloat(float):
    def __repr__(self):
        return "999.0"

    def __float__(self):
        return 999.0


class Color(enum.Enum):
    RED = "r"


class Level(enum.IntEnum):
    HIGH = 3


class Kind(enum.StrEnum):
    USER = "user"


@dataclass
class Data:
    a: int = 1


@pytest.fixture
def stdlib_calls(monkeypatch):
    """Record every fall back to the stdlib ``__init__``."""
    calls = []
    original = json_response._stdlib_init

    def spy(self, data, **kwargs):
        calls.append(data)
        original(self, data, **kwargs)

    monkeypatch.setattr(json_response, "_stdlib_init", spy)
    return calls


def _stdlib_bytes(data):
    return json.dumps(data, cls=DjangoJSONEncoder).encode()


def test_installed_on_the_class():
    assert JsonResponse.__init__ is json_response._orjson_init


@pytest.mark.parametrize("data", [
    {"s": "plain", "accents": "é ü 中", "n": 1, "f": 1.5, "b": True, "none": None, "list": [1, [2, {"a": 3}]]},
    {"tuple": (1, 2), "point": Point(1, 2), "nested": [Point(3, 4)]},
    {"uuid": UUID("12345678-1234-5678-1234-567812345678")},
    {"td": timedelta(days=1, hours=2, microseconds=5), "dec": Decimal("1.10")},
    {"lazy": lazy(lambda: "lazy text", str)(), "safe": mark_safe("<b>")},
    {"date": date(2026, 10, 28), "time": time(10, 43, 25), "time_us": time(10, 43, 25, 633818)},
    {"int_enum": Level.HIGH, "str_enum": Kind.USER},
    {"ordered": OrderedDict(b=1, a=2), "default": defaultdict(list, a=[1])},
    {"items_dict": ItemsDict(a=1), "query": QueryDict("a=1&a=2"), "multi": MultiValueDict({"a": ["1", "2"]})},
    {"iter_list": IterList([1, 2]), "iter_tuple": IterTuple((1, 2))},
    {"str_str": StrStr("raw"), "repr_int": ReprInt(5), "sub_float": SubFloat(1.5)},
])
def test_same_value_as_stdlib_without_fallback(data, stdlib_calls):
    response = JsonResponse(data)
    assert stdlib_calls == []
    assert response["Content-Type"] == "application/json"
    assert orjson.loads(response.content) == json.loads(_stdlib_bytes(data))


@pytest.mark.parametrize("value", [
    datetime(2026, 10, 28, 10, 43, 25, 633818, tzinfo=UTC),
    datetime(2026, 10, 28, 10, 43, 25, tzinfo=UTC),
    datetime(2026, 10, 28, 10, 43, 25, 633818, tzinfo=timezone(timedelta(hours=2))),
    datetime(2026, 10, 28, 10, 43, 25, 633818),
    time(10, 43, 25, 633818),
])
def test_datetime_strings_match_django_exactly(value, stdlib_calls):
    response = JsonResponse({"v": value})
    assert stdlib_calls == []
    assert orjson.loads(response.content)["v"] == DjangoJSONEncoder().default(value)


def test_utc_datetime_uses_milliseconds_and_z():
    response = JsonResponse({"v": datetime(2026, 10, 28, 10, 43, 25, 633818, tzinfo=UTC)})
    assert response.content == b'{"v":"2026-10-28T10:43:25.633Z"}'


def test_list_with_safe_false(stdlib_calls):
    response = JsonResponse([1, {"a": Point(1, 2)}], safe=False)
    assert stdlib_calls == []
    assert orjson.loads(response.content) == [1, {"a": [1, 2]}]


def test_safe_rejects_non_dict():
    with pytest.raises(TypeError, match="safe parameter to False"):
        JsonResponse([1, 2])


def test_kwargs_are_forwarded():
    response = JsonResponse({"error": "x"}, status=400, headers={"X-Test": "1"})
    assert response.status_code == 400
    assert response["X-Test"] == "1"
    assert response["Content-Type"] == "application/json"


def test_nan_value_is_null(stdlib_calls):
    response = JsonResponse({"v": float("nan")})
    assert stdlib_calls == []
    assert response.content == b'{"v":null}'


def test_plain_enum_is_encoded_as_its_value(stdlib_calls):
    # The one accepted difference: the stdlib raises TypeError here.
    response = JsonResponse({"v": Color.RED})
    assert stdlib_calls == []
    assert response.content == b'{"v":"r"}'


def _deep(levels):
    root = node = []
    for _ in range(levels):
        child = []
        node.append(child)
        node = child
    return root


@pytest.mark.parametrize("data", [
    {7: "int key", True: "bool key", None: "none key"},
    {1e-7: "a", 1e-5: "b", 2.5: "c", math.inf: "d", math.nan: "e"},
    {Kind.USER: "str enum key", mark_safe("k"): "str subclass key"},
    {"name": "bad\udcffname"},
    {"big": 2**70},
    {"deep": _deep(300)},
])
def test_orjson_limits_fall_back_to_stdlib(data, stdlib_calls):
    response = JsonResponse(data)
    assert len(stdlib_calls) == 1
    assert response.content == _stdlib_bytes(data)


@pytest.mark.parametrize(("data", "error"), [
    ({"set": {1, 2}}, TypeError),
    ({"bytes": b"x"}, TypeError),
    ({"dataclass": Data()}, TypeError),
    ({"aware_time": time(10, 0, tzinfo=UTC)}, ValueError),
    ({Color.RED: 1}, TypeError),
    ({datetime(2026, 1, 1, tzinfo=UTC): 1}, TypeError),
    ({UUID(int=1): 1}, TypeError),
])
def test_unserializable_values_raise_like_stdlib(data, error, stdlib_calls):
    with pytest.raises(error):
        JsonResponse(data)
    assert len(stdlib_calls) == 1


def test_custom_encoder_uses_stdlib(stdlib_calls):
    class Encoder(DjangoJSONEncoder):
        def default(self, o):
            if isinstance(o, set):
                return sorted(o)
            return super().default(o)

    response = JsonResponse({"set": {2, 1}}, encoder=Encoder)
    assert len(stdlib_calls) == 1
    assert response.content == b'{"set": [1, 2]}'


def test_json_dumps_params_use_stdlib(stdlib_calls):
    response = JsonResponse({"a": 1}, json_dumps_params={"indent": 2})
    assert len(stdlib_calls) == 1
    assert response.content == b'{\n  "a": 1\n}'
