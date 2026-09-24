# `update-session <SESSION_ID> annotations` — edit the annotations

Edit the session's free-form JSON annotations with ordered operations. MCP tool: `mcp__twicc__update_session_annotations`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC update-session '<SESSION_ID>' annotations <OPERATION>...
```

Operations apply left-to-right:

- `clear` — replace annotations with `{}`.
- `replace-file:PATH` — replace annotations with a JSON object file.
- `merge-file:PATH` — recursively merge a JSON object file.
- `set:KEY=VALUE` — set a scalar value; dotted keys are supported.
- `unset:KEY` — remove a key; dotted keys are supported and missing paths are ignored.

`set:` parses `true`, `false`, `null`, numbers, and strings. Use `merge-file:` or `replace-file:` for list or object values.

## Errors

- Local (exit 1): `invalid_annotation_operation`, `invalid_annotation`, `invalid_annotation_path`, `annotation_path_conflict`, `annotation_non_scalar`, `invalid_annotations_file`, `no_op`.
- Server (exit 3): the local codes above, re-checked server-side, plus `invalid_annotations`.

## Examples

```bash
$TWICC update-session 4a8352fb-... annotations set:role=reviewer unset:temporary
$TWICC update-session 4a8352fb-... annotations set:note="Needs backend review"
$TWICC update-session 4a8352fb-... annotations unset:foo set:foo.point=bar
$TWICC update-session 4a8352fb-... annotations replace-file:/tmp/base.json merge-file:/tmp/extra.json
$TWICC update-session 4a8352fb-... annotations clear
$TWICC update-session self annotations set:role=worker
```

## Related commands

- `$TWICC update-sessions annotations` — the same operations on several sessions. Skill: `twicc-update-sessions`.
- `$TWICC session <ID>` — `annotations` in the row. Skill: `twicc-session`.

## How to present results

1. On `no_op`, the error message is self-explanatory.
