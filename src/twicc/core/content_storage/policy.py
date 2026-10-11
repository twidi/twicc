"""Stable identity for the compression attempt policy."""

from twicc.core.content_storage.codec import COMPRESSION_LEVEL, ContentIntegrityError, _load_dictionary


def compression_policy_version(provider: str, dictionary_id: int) -> str:
    dictionary = _load_dictionary(dictionary_id)
    if dictionary.provider != provider:
        raise ContentIntegrityError('Content dictionary provider mismatch')
    return (
        f'zstd:level={COMPRESSION_LEVEL}:dictionary={bytes(dictionary.sha256).hex()}'
        ':format=raw:savings=compressed-size<raw-size'
    )
