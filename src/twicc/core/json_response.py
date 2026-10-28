"""Serialize Django's ``JsonResponse`` with orjson instead of the stdlib ``json``.

``install()`` replaces ``JsonResponse.__init__`` on the class itself, so every
existing ``from django.http import JsonResponse`` gets it, whatever the import order.

The output parses to the same value as ``json.dumps(data, cls=DjangoJSONEncoder)``.
Date/time values, ``timedelta``, ``Decimal`` and lazy strings go through
``DjangoJSONEncoder.default`` (millisecond truncation, ``Z`` suffix). Subclasses of
builtins (other than ``Enum`` members, which orjson encodes natively) and dataclasses
also go through ``default``, which encodes them the way the stdlib C encoder does.
The bytes differ: compact separators, raw UTF-8, ``NaN`` -> ``null``.

Everything orjson rejects falls back to the original stdlib ``__init__``, which gives
the same output or raises the same exception as before:

- dict keys that are not exact ``str``, ``StrEnum`` and other ``str`` subclasses included
  (orjson has no hook for keys, and the stdlib formats float keys its own way);
- strings with lone surrogates (non UTF-8 file names);
- integers beyond 64 bits, nesting deeper than 254 levels;
- values neither encoder supports (``set``, ``bytes``, aware ``time``, dataclasses...).

One known difference: orjson encodes a plain ``Enum`` member as its value, where the
stdlib raises ``TypeError``. ``IntEnum``, ``StrEnum`` and Django choices give the same output.

Speed: a payload with an ``int`` key takes the whole stdlib path, as before this module.
Dict subclasses (``OrderedDict``, ``defaultdict``) are copied in Python by ``default``:
slower than exact dicts, usually still faster than the stdlib (not when empty).
"""

import orjson
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, JsonResponse

_OPTIONS = orjson.OPT_PASSTHROUGH_DATETIME | orjson.OPT_PASSTHROUGH_SUBCLASS | orjson.OPT_PASSTHROUGH_DATACLASS
_django_default = DjangoJSONEncoder().default
_stdlib_init = JsonResponse.__init__


def _default(obj):
    # Subclasses of builtins: mirror what the stdlib C encoder reads from them.
    if isinstance(obj, dict):
        return dict(obj.items())  # items() on a dict subclass (QueryDict gives the last value)
    if isinstance(obj, (list, tuple)):
        return list(obj)  # iteration on a subclass (a NamedTuple becomes a list)
    if isinstance(obj, str):
        return str.__str__(obj)  # the raw string, not an overridden __str__
    if isinstance(obj, int):
        return int.__index__(obj)  # the raw integer, not an overridden __repr__
    if isinstance(obj, float):
        return float.__float__(obj)
    return _django_default(obj)


def _orjson_init(self, data, encoder=DjangoJSONEncoder, safe=True, json_dumps_params=None, **kwargs):
    if encoder is not DjangoJSONEncoder or json_dumps_params:
        _stdlib_init(self, data, encoder=encoder, safe=safe, json_dumps_params=json_dumps_params, **kwargs)
        return
    if safe and not isinstance(data, dict):
        raise TypeError("In order to allow non-dict objects to be serialized set the safe parameter to False.")
    try:
        content = orjson.dumps(data, default=_default, option=_OPTIONS)
    except orjson.JSONEncodeError:
        _stdlib_init(self, data, encoder=encoder, safe=safe, json_dumps_params=json_dumps_params, **kwargs)
        return
    kwargs.setdefault("content_type", "application/json")
    HttpResponse.__init__(self, content=content, **kwargs)


def install():
    """Make ``JsonResponse`` serialize with orjson. Idempotent."""
    JsonResponse.__init__ = _orjson_init
