FROM golang:1.26.4-bookworm@sha256:b305420a68d0f229d91eb3b3ed9e519fcf2cf5461da4bef997bf927e8c0bfd2b AS guard-build
WORKDIR /src
COPY go.mod ./
COPY cmd/ cmd/
COPY internal/ internal/
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /semselect ./cmd/semselect

FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55 AS frozen-ubuntu
# Bootstrap TLS from the pinned Go image; all installed packages come from the snapshot.
COPY --from=guard-build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN sed -i -E 's|^URIs:.*|URIs: https://snapshot.ubuntu.com/ubuntu/20261005T000000Z|' /etc/apt/sources.list.d/ubuntu.sources \
    && echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/50snapshot

# Model files are acquired separately; no credentials or weights in the image.
FROM frozen-ubuntu AS llama-build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl --fail --location --retry 3 https://api.github.com/repos/ggml-org/llama.cpp/tarball/6c59c40076c00eab49754dc955d7652d93f9e125 -o llama.tar.gz \
    && echo 'efe5ae8270793300807cd2c0e20e748816f2c153971ce5610450f0e6a23d214e  llama.tar.gz' | sha256sum -c - \
    && tar xzf llama.tar.gz --strip-components=1 && rm llama.tar.gz
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF \
    -DGGML_CUDA=OFF -DGGML_METAL=OFF -DLLAMA_OPENSSL=OFF \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_UI=OFF \
    && cmake --build build --target llama-server -j 4

FROM frozen-ubuntu AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=llama-build /src/build/bin/ /opt/llama/
COPY --from=llama-build /src/LICENSE /usr/share/licenses/llama.cpp/LICENSE
COPY licenses/ /usr/share/licenses/semselect/
ENV LD_LIBRARY_PATH=/opt/llama
LABEL org.opencontainers.image.source="https://github.com/C360Studio/semselect" \
      io.semselect.llama-revision="6c59c40076c00eab49754dc955d7652d93f9e125"
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/opt/llama/llama-server"]

FROM scratch AS service
COPY --from=guard-build /semselect /semselect
USER 65532:65532
EXPOSE 8084
ENTRYPOINT ["/semselect"]
