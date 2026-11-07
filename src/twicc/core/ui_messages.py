"""Presentation metadata for browser consumers, without backend translation.

English strings remain usable by CLI/MCP consumers. Browser transports explicitly
publish metadata; serializers do not inspect or translate English text.
"""


def ui_message(key: str, params: dict | None = None, *, count: int | None = None) -> dict:
    descriptor = {"key": key, "params": {} if params is None else params}
    if count is not None:
        descriptor["count"] = count
    return descriptor


def ui_error(message: str, key: str, params: dict | None = None, **extra) -> dict:
    return {"error": message, "ui_message": ui_message(key, params), **extra}


class UiMessageText(str):
    """An English string with metadata that ordinary JSON serialization ignores."""

    def __new__(cls, message: str, key: str, params: dict | None = None, *, count: int | None = None):
        value = super().__new__(cls, message)
        value.ui_message = ui_message(key, params, count=count)
        return value

    def __reduce__(self):
        return (_restore_ui_message_text, (str(self), self.ui_message))


def _restore_ui_message_text(message: str, descriptor: dict) -> UiMessageText:
    return UiMessageText(message, descriptor['key'], descriptor['params'], count=descriptor.get('count'))


def ui_message_text(message: str, key: str, params: dict | None = None, *, count: int | None = None) -> str:
    return UiMessageText(message, key, params, count=count)


class UiMessageError(ValueError):
    """A domain validation failure with a separate browser presentation descriptor."""

    def __init__(self, message: str, key: str, params: dict | None = None):
        super().__init__(message)
        self.ui_message = ui_message(key, params)

    def __reduce__(self):
        return (type(self), (str(self), self.ui_message['key'], self.ui_message['params']))
