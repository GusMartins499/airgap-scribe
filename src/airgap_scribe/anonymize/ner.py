from collections.abc import Sequence
from functools import lru_cache

import spacy
from spacy.language import Language

UNUSED_COMPONENTS = ("parser", "lemmatizer", "morphologizer", "attribute_ruler", "senter")

EntitySpan = tuple[int, int, str]


@lru_cache(maxsize=4)
def load_pipeline(model: str) -> Language:
    return spacy.load(model, exclude=list(UNUSED_COMPONENTS))


def find_entities(
    texts: Sequence[str], model: str, labels: Sequence[str]
) -> list[list[EntitySpan]]:
    wanted = set(labels)
    pipeline = load_pipeline(model)
    return [
        [(ent.start_char, ent.end_char, ent.label_) for ent in doc.ents if ent.label_ in wanted]
        for doc in pipeline.pipe(texts)
    ]
