# Build context is eval/specialist-intent. Both build arguments are mandatory.
# Resolve a Linux ARM64 Python image digest and a hash-locked complete wheel
# closure during bounded setup; no floating dependencies enter the frozen image.
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ARG PYTHON_IMAGE
RUN case "$PYTHON_IMAGE" in *@sha256:*) ;; *) exit 1;; esac
ENV OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
    TOKENIZERS_PARALLELISM=false HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /experiment
COPY requirements.lock.txt /experiment/requirements.lock.txt
COPY wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels --require-hashes -r requirements.lock.txt \
    && rm -rf /wheels
COPY adapters.py preflight.py server.py ./
COPY locks ./locks
USER 65532:65532
ENTRYPOINT ["python", "server.py", "--host", "0.0.0.0"]
