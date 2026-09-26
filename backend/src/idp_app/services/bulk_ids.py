"""Bound SQL fan-out independently of the logical batch size."""

from collections.abc import Iterator

BULK_READ_SIZE = 100
MAX_WORK_BATCH_SIZE = 1000


def id_chunks(identities: list[str]) -> Iterator[list[str]]:
    unique = list(dict.fromkeys(identities))
    if len(unique) > MAX_WORK_BATCH_SIZE:
        raise ValueError("A work batch supports at most 1,000 distinct documents")
    for offset in range(0, len(unique), BULK_READ_SIZE):
        yield unique[offset : offset + BULK_READ_SIZE]


def id_parameters(identities: list[str]) -> tuple[str, dict[str, object]]:
    values: dict[str, object] = {
        f"id_{index}": identity for index, identity in enumerate(identities)
    }
    return ", ".join(f":{name}" for name in values), values
