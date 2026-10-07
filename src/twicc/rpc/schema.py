"""Introspect Click parameters into ParamSpecs + JSON Schema fragments."""

from __future__ import annotations

from typing import NamedTuple

import click


class ParamSpec(NamedTuple):
    name: str                       # body field name (click param.name)
    kind: str                       # "argument" | "option"
    is_flag: bool
    multiple: bool
    variadic: bool                  # nargs == -1
    required: bool
    json_type: str                  # string|integer|number|boolean|array|enum
    choices: tuple[str, ...] | None
    opt: str | None                 # primary option string, e.g. "--limit"
    secondary_opt: str | None       # off-form, e.g. "--no-thinking"
    help: str
    hidden: bool = False            # accepted compatibility option, omitted from discovery


_TYPE_MAP = {"integer": "integer", "float": "number", "boolean": "boolean"}


def _base_type(param: click.Parameter) -> tuple[str, tuple[str, ...] | None]:
    t = param.type
    if isinstance(t, click.Choice):
        return "enum", tuple(str(c) for c in t.choices)
    return _TYPE_MAP.get(getattr(t, "name", "text"), "string"), None


def param_spec(param: click.Parameter) -> ParamSpec | None:
    """Translate one Click parameter, or None if it should be skipped."""
    # Skip eager/meta params (--version, --help, --install-completion, ...).
    hidden = bool(getattr(param, "hidden", False))
    if getattr(param, "is_eager", False):
        return None
    # Only these retired flags remain part of the API contract. Other hidden
    # CLI options stay local-only.
    if hidden and param.name not in {"slim", "paginated"}:
        return None
    if param.name in {"version", "help"}:
        return None

    base, choices = _base_type(param)
    is_flag = bool(getattr(param, "is_flag", False))
    multiple = bool(getattr(param, "multiple", False))
    variadic = getattr(param, "nargs", 1) == -1

    if param.param_type_name == "argument":
        kind, opt, secondary = "argument", None, None
    else:
        kind = "option"
        opt = param.opts[0] if param.opts else None
        secondary = param.secondary_opts[0] if param.secondary_opts else None

    if multiple or variadic:
        json_type = "array"
    elif is_flag:
        json_type = "boolean"
    else:
        json_type = base

    return ParamSpec(
        name=param.name,
        kind=kind,
        is_flag=is_flag,
        multiple=multiple,
        variadic=variadic,
        required=bool(getattr(param, "required", False)),
        json_type=json_type,
        choices=choices,
        opt=opt,
        secondary_opt=secondary,
        help=(getattr(param, "help", "") or ""),
        hidden=hidden,
    )


def json_schema_for(params: list[ParamSpec], *, include_hidden: bool = False) -> dict:
    """Build a JSON Schema object for a request body from its ParamSpecs."""
    props: dict = {}
    required: list[str] = []
    for p in params:
        if p.hidden and not include_hidden:
            continue
        if p.json_type == "array":
            schema: dict = {"type": "array", "items": {"type": "string"}}
        elif p.json_type == "enum":
            schema = {"type": "string", "enum": list(p.choices or ())}
        else:
            schema = {"type": p.json_type}
        if p.help:
            schema["description"] = p.help
        props[p.name] = schema
        if p.required:
            required.append(p.name)
    out: dict = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out
