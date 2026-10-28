ARG PYTHON_IMAGE=python:3.12-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.6

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS whisper-build

ARG WHISPER_CPP_VERSION=v1.9.5
ARG WHISPER_CPP_COMMIT=d1be6fde11ac6e0407606b4e42fe72d34add8037

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates cmake g++ git make \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${WHISPER_CPP_VERSION}" \
        https://github.com/ggml-org/whisper.cpp.git /src/whisper.cpp \
    && test "$(git -C /src/whisper.cpp rev-parse HEAD)" = "${WHISPER_CPP_COMMIT}"

RUN cmake -S /src/whisper.cpp -B /src/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/whisper \
        -DCMAKE_INSTALL_RPATH=/opt/whisper/lib \
        -DBUILD_SHARED_LIBS=ON \
        -DGGML_NATIVE=OFF \
        -DGGML_BACKEND_DL=ON \
        -DGGML_CPU_ALL_VARIANTS=ON \
        -DGGML_BACKEND_DIR=/opt/whisper/lib \
        -DGGML_OPENMP=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_SERVER=OFF \
        -DWHISPER_SDL2=OFF \
        -DWHISPER_CURL=OFF \
    && cmake --build /src/build --config Release --parallel "$(nproc)" \
    && cmake --install /src/build --strip

FROM ${PYTHON_IMAGE} AS python-build

ARG SPACY_MODEL=pt_core_news_lg
ARG SPACY_MODEL_VERSION=3.8.0
ARG SPACY_MODEL_SHA256=2561c9a72a938d37141e9694e1a36d25061a44ce7e4f3bad2d3fa3bb836191af

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY data/names/first-names.txt ./data/names/first-names.txt
RUN uv sync --locked --no-dev --no-editable

ADD --checksum=sha256:${SPACY_MODEL_SHA256} \
    https://github.com/explosion/spacy-models/releases/download/${SPACY_MODEL}-${SPACY_MODEL_VERSION}/${SPACY_MODEL}-${SPACY_MODEL_VERSION}-py3-none-any.whl \
    /tmp/
RUN uv pip install --python /opt/venv/bin/python --no-deps \
        "/tmp/${SPACY_MODEL}-${SPACY_MODEL_VERSION}-py3-none-any.whl" \
    && /opt/venv/bin/python -c "import spacy, sys; sys.exit(0 if spacy.util.is_package('${SPACY_MODEL}') else 1)"

FROM ${PYTHON_IMAGE} AS runtime

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --user-group scribe

COPY --from=whisper-build /opt/whisper/lib /opt/whisper/lib
COPY --from=whisper-build /opt/whisper/bin/whisper-cli /opt/whisper/bin/whisper-cli
COPY --from=python-build /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:/opt/whisper/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER scribe
WORKDIR /work

ENTRYPOINT ["airgap-scribe"]
CMD ["--help"]
