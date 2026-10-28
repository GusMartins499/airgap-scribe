import random
import re
from collections import Counter
from dataclasses import dataclass

from airgap_scribe.anonymize.mapping import significant_words

SEED = 20261109
TRANSCRIPT_COUNT = 240

FEMALE_NAMES = (
    "Ana",
    "Maria",
    "Juliana",
    "Fernanda",
    "Patrícia",
    "Luíza",
    "Jéssica",
    "Raquel",
    "Isabela",
    "Heloísa",
    "Rafaela",
    "Yasmin",
    "Beatriz",
    "Camila",
    "Tereza",
    "Sônia",
    "Aparecida",
    "Débora",
    "Thalita",
    "Odete",
    "Dalva",
    "Cristiane",
    "Letícia",
    "Vitória",
    "Giovana",
    "Lorena",
    "Marlene",
    "Simone",
    "Priscila",
    "Natália",
)
MALE_NAMES = (
    "João",
    "José",
    "Thiago",
    "Matheus",
    "Vinícius",
    "Kauã",
    "Wellington",
    "Rafael",
    "Gustavo",
    "Rodrigo",
    "Sebastião",
    "Benedito",
    "Felipe",
    "Yago",
    "Benício",
    "Henrique",
    "Lucas",
    "Marcos",
    "Antônio",
    "Raimundo",
    "Davi",
    "Caio",
    "Leandro",
    "Edson",
    "Cícero",
    "Otávio",
    "Renato",
    "Joaquim",
    "Severino",
    "Igor",
)
MISSPELLINGS = {
    "Luíza": ("Luiza", "Luisa"),
    "Jéssica": ("Jessica", "Géssica"),
    "Raquel": ("Raquéu",),
    "Isabela": ("Izabela", "Isabella"),
    "Heloísa": ("Eloísa", "Heloiza"),
    "Rafaela": ("Rafaella",),
    "Yasmin": ("Iasmin", "Yasmim"),
    "Tereza": ("Teresa",),
    "Thalita": ("Talita",),
    "Letícia": ("Leticia",),
    "Giovana": ("Geovana",),
    "Thiago": ("Tiago",),
    "Matheus": ("Mateus",),
    "Vinícius": ("Vinicios",),
    "Kauã": ("Cauã", "Kaua"),
    "Wellington": ("Welinton", "Uelinton"),
    "Yago": ("Iago",),
    "Benício": ("Benicio",),
    "Henrique": ("Enrique",),
    "Antônio": ("Antonio",),
    "Otávio": ("Otavio",),
}
SURNAMES = (
    "Silva",
    "Souza",
    "Oliveira",
    "Ferreira",
    "Almeida",
    "Carvalho",
    "Ribeiro",
    "Nascimento",
    "Moreira",
    "Barbosa",
    "Cavalcanti",
    "Rezende",
    "Albuquerque",
    "Medeiros",
)
PLACES = (
    "Vila Nova do Sertão",
    "Campo Belo do Sul",
    "Jardim Aurora",
    "Santa Luzia do Leste",
    "Morro das Andorinhas",
    "Rio Claro do Norte",
    "Parque das Palmeiras",
    "Lagoa Serena",
    "Jardim Primavera",
    "Serra Azul do Oeste",
    "Porto Esmeralda",
    "Vale dos Ipês",
    "Itaquara",
    "Piraúna",
    "Boa Esperança do Leste",
    "Conceição das Pedras",
)
SPEAKER_SENTENCES = (
    "Eu lembro quando {pa} {p} chegou em {l} pela primeira vez.",
    "{Pa} {p} sempre dizia que a gente precisava ter paciência.",
    "Aí eu falei com {pa} {p} e {pr} concordou comigo.",
    "A gente se mudou pra {l} quando {pa} {p} tinha seis anos.",
    "{p} contou que o trabalho em {l} foi muito difícil no começo.",
    "Então {pa} {p} e {qa} {q} começaram a conversar sobre isso.",
    "Obrigado, {p}, pela sua fala.",
    "Eu queria agradecer {pa} {p} {s}, que organizou tudo.",
    "Meu filho {q} está na escola lá de {l} agora.",
    "Quando eu era criança em {l}, minha avó {p} cuidava de todo mundo.",
    "{Pa} {p} perguntou se a gente ia voltar pra {l}.",
    "Bom dia a todos, meu nome é {p} {s} e eu moro em {l}.",
    "{Pa} {p} falou uma coisa que me marcou muito.",
    "Eu concordo com o que {pa} {p} falou agora há pouco.",
    "Depois a gente encontrou {pa} {p} na feira de {l}.",
    "E {pa} {p}, né, não queria ir de jeito nenhum.",
    "{p}, você quer falar um pouco?",
    "Minha filha {q} fez aniversário semana passada.",
    "Eu e {pa} {p} trabalhamos juntos muitos anos em {l}.",
    "Ontem {pa} {p} me ligou e a gente conversou bastante.",
    "Lá em {l} tinha uma praça onde {pa} {p} brincava.",
    "Eu nunca vou esquecer o que {pa} {p} fez por mim.",
)
FILLER_SENTENCES = (
    "Isso foi muito importante pra mim.",
    "A gente aprendeu muito naquela época.",
    "Eu acho que cada um tem o seu tempo.",
    "Foi uma fase complicada, mas passou.",
    "Hoje eu vejo tudo de outro jeito.",
    "Não é fácil falar disso, mas eu vou tentar.",
    "Eu fiquei pensando nisso a semana inteira.",
    "Tem dia que é mais difícil, tem dia que é mais leve.",
    "A conversa de hoje me ajudou bastante.",
    "Eu queria dizer que estou feliz de estar aqui.",
)


@dataclass(frozen=True)
class Person:
    first_name: str
    surname: str
    article: str
    pronoun: str


@dataclass(frozen=True)
class SyntheticTranscript:
    kind: str
    text: str
    sensitive_words: frozenset[str]


def generate_corpus(count: int = TRANSCRIPT_COUNT, seed: int = SEED) -> list[SyntheticTranscript]:
    rng = random.Random(seed)
    return [_transcript(rng, "talk" if index % 2 == 0 else "discussion") for index in range(count)]


def word_occurrences(text: str, words: frozenset[str]) -> Counter[tuple[int, str]]:
    counts: Counter[tuple[int, str]] = Counter()
    for number, line in enumerate(text.splitlines(), start=1):
        for word in re.findall(r"[^\W\d_]+", line):
            if word in words:
                counts[(number, word)] += 1
    return counts


def _transcript(rng: random.Random, kind: str) -> SyntheticTranscript:
    cast = [_person(rng) for _ in range(rng.randint(3, 6))]
    places = rng.sample(PLACES, rng.randint(1, 3))
    used: set[str] = set()
    sentences = [_sentence(rng, cast, places, used) for _ in range(rng.randint(18, 32))]
    text = _paragraphs(rng, sentences) if kind == "talk" else _turns(rng, sentences)
    words = frozenset(word for surface in used for word in significant_words(surface))
    return SyntheticTranscript(kind=kind, text=text, sensitive_words=words)


def _person(rng: random.Random) -> Person:
    if rng.random() < 0.5:
        return Person(rng.choice(FEMALE_NAMES), rng.choice(SURNAMES), "a", "ela")
    return Person(rng.choice(MALE_NAMES), rng.choice(SURNAMES), "o", "ele")


def _spoken(rng: random.Random, name: str) -> str:
    variants = MISSPELLINGS.get(name)
    if variants and rng.random() < 0.3:
        return rng.choice(variants)
    return name


def _sentence(rng: random.Random, cast: list[Person], places: list[str], used: set[str]) -> str:
    if rng.random() < 0.3:
        return rng.choice(FILLER_SENTENCES)
    first, second = rng.sample(cast, 2)
    values = {
        "p": _spoken(rng, first.first_name),
        "pa": first.article,
        "Pa": first.article.upper(),
        "pr": first.pronoun,
        "q": _spoken(rng, second.first_name),
        "qa": second.article,
        "s": first.surname,
        "l": rng.choice(places),
    }
    template = rng.choice(SPEAKER_SENTENCES)
    for key in ("p", "q", "s", "l"):
        if "{" + key + "}" in template:
            used.add(values[key])
    return template.format(**values)


def _paragraphs(rng: random.Random, sentences: list[str]) -> str:
    paragraphs = []
    while sentences:
        size = rng.randint(2, 5)
        paragraphs.append(" ".join(sentences[:size]))
        sentences = sentences[size:]
    return "\n\n".join(paragraphs) + "\n"


def _turns(rng: random.Random, sentences: list[str]) -> str:
    speakers = rng.randint(2, 5)
    turns = []
    while sentences:
        size = rng.randint(1, 3)
        speaker = rng.randint(1, speakers)
        turns.append(f"Speaker {speaker}: " + " ".join(sentences[:size]))
        sentences = sentences[size:]
    return "\n".join(turns) + "\n"
