FROM python:3.12-slim

WORKDIR /app

# ── System deps ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    busybox-static \
    && rm -rf /var/lib/apt/lists/*

# ── Busybox hardening ──────────────────────────────────────────────────────
# Note: heredoc syntax not supported by classic Docker builder — using printf.
RUN WRAPPER=/usr/local/bin/bb && \
    printf '%s\n' \
        '#!/bin/sh' \
        '# BeigeBox restricted busybox wrapper.' \
        'set -e' \
        '' \
        'BLOCKED_APPLETS="rm rmdir mv cp chmod chown chroot ln mknod mkfifo mktemp dd truncate tee install shred sync mount umount su sudo su-exec adduser addgroup passwd pivot_root switch_root nsenter unshare ash sh bash csh tcsh nc netcat telnet tftp ftpget ftpput wget httpd inetd kill killall pkill reboot poweroff halt insmod rmmod modprobe awk sed xargs find"' \
        '' \
        'APPLET="$1"' \
        'if [ -z "$APPLET" ]; then echo "Usage: bb <applet> [args...]" >&2; exit 1; fi' \
        '' \
        'for blocked in $BLOCKED_APPLETS; do' \
        '    if [ "$APPLET" = "$blocked" ]; then echo "bb: $APPLET is not permitted" >&2; exit 1; fi' \
        'done' \
        '' \
        'shift' \
        'exec /bin/busybox "$APPLET" "$@"' \
    > $WRAPPER && \
    chmod 755 $WRAPPER && \
    chown root:root $WRAPPER

# ── Non-root user ──────────────────────────────────────────────────────────
RUN useradd -m -u 1000 -s /usr/sbin/nologin appuser

# ── Python deps ────────────────────────────────────────────────────────────
COPY requirements.txt .
COPY config.yaml .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir ddgs

# ── Application code ───────────────────────────────────────────────────────
COPY beigebox/ beigebox/
COPY docker/config.docker.yaml config.yaml
COPY scripts/ scripts/
COPY hooks/ hooks/

# ── Data directory ─────────────────────────────────────────────────────────
RUN mkdir -p /app/data && chown -R appuser:appuser /app/data

USER appuser

EXPOSE 8000

CMD ["python", "-m", "beigebox", "dial"]

LABEL org.opencontainers.image.licenses="AGPL-3.0"
LABEL maintainer="Ryan L. <ryan.a.labarge+ghBeigebox@gmail.com>"
LABEL commercial_licensing="Reach out for proprietary use cases -- free otherwise"
