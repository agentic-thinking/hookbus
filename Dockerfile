FROM python:3.12-slim

LABEL maintainer="Agentic Thinking Limited"
LABEL description="HookBus - Universal event bus for AI agent lifecycle enforcement"
LABEL org.opencontainers.image.source="https://github.com/agentic-thinking/hookbus"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.title="HookBus"
LABEL org.opencontainers.image.version="0.1.0"
LABEL org.opencontainers.image.vendor="Agentic Thinking Limited"
LABEL org.opencontainers.image.documentation="https://github.com/agentic-thinking/hookbus"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY hookbus/ /opt/hookbus/hookbus/
COPY hookbus.yaml /opt/hookbus/

WORKDIR /opt/hookbus

# Non-root runtime user (shared uid across hookbus + subscribers so named volume mounts work)
RUN groupadd --system --gid 10001 hookbus \
 && useradd  --system --uid 10001 --gid hookbus --home-dir /home/hookbus --create-home --shell /usr/sbin/nologin hookbus \
 && mkdir -p /root/.hookbus \
 && chown -R hookbus:hookbus /root/.hookbus /opt/hookbus
RUN chmod 755 /root

EXPOSE 18800

COPY entrypoint.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/entrypoint.sh

USER hookbus

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:18800/', timeout=3); sys.exit(0 if r.status in (200,401) else 1)" || exit 1

ENTRYPOINT ["entrypoint.sh"]
