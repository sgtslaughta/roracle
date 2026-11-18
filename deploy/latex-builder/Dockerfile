# latex-builder (docs/enterprise/latex-builder.md). Mirror: build with --build-arg BASE=<mirror>/texlive/texlive:...
# Pinned by tag + digest; bump both together. Runs as uid 1001 with a read-only root FS and a tmpfs /tmp.
ARG BASE=texlive/texlive:latest-small@sha256:c572bf2d14631545769e5a8f8d315e7b8f8896b14720b1c7a063917d503fd46a
FROM ${BASE}
RUN useradd --uid 1001 --no-create-home --shell /usr/sbin/nologin latex
COPY server.py /opt/latex-builder/server.py
USER 1001
ENV PORT=8080 TMPDIR=/tmp
EXPOSE 8080
HEALTHCHECK CMD python3 -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8080/health',timeout=3)"
ENTRYPOINT ["python3", "-I", "/opt/latex-builder/server.py"]
