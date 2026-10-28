from collections.abc import Sequence


def find_entities(text: str, model: str, labels: Sequence[str]) -> list[tuple[str, str]]:
    raise NotImplementedError
