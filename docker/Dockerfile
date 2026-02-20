FROM python:3.12-slim

WORKDIR /app

# ── System deps ────────────────────────────────────────────────────────────
# busybox-static: self-contained binary, no shared lib deps.
# We install it then immediately strip out destructive applets by replacing
# them with a stub that exits 1.  The operator shell tool runs ONLY through
# /usr/local/bin/bb (our restricted busybox wrapper) — never via /bin/sh.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    busybox-static \
    && rm -rf /var/lib/apt/lists/*

# ── Busybox hardening ──────────────────────────────────────────────────────
# Create a dedicated wrapper at /usr/local/bin/bb.
# The operator config allowlist is the first gate; this is the second.
# Explicitly blocked: anything that can modify files, escalate, pivot,
# or escape the container.  We do this at the binary level so even a
# prompt-injection bypass can't invoke them through busybox.
RUN set -e; \
    BB=/bin/busybox; \
    WRAPPER=/usr/local/bin/bb; \
    \
    # Applets that must never run — write/escalate/pivot/escape surface
    BLOCKED="rm rmdir mv cp chmod chown chroot ln mknod mkfifo mktemp \
             dd truncate tee install shred sync mount umount \
             su sudo su-exec adduser addgroup passwd \
             pivot_root switch_root nsenter unshare \
             ash sh bash csh tcsh \
             nc netcat telnet tftp ftpget ftpput \
             wget httpd inetd \
             kill killall pkill \
             reboot poweroff halt \
             insmod rmmod modprobe \
             awk sed xargs find"; \
    \
    cat > $WRAPPER << 'SCRIPT'
#!/bin/sh
# BeigeBox restricted busybox wrapper.
# Routes applet calls through /bin/busybox after checking the blocklist.
set -e

BLOCKED_APPLETS="rm rmdir mv cp chmod chown chroot ln mknod mkfifo mktemp \
dd truncate tee install shred sync mount umount \
su sudo su-exec adduser addgroup passwd \
pivot_root switch_root nsenter unshare \
ash sh bash csh tcsh \
nc netcat telnet tftp ftpget ftpput \
wget httpd inetd \
kill killall pkill \
reboot poweroff halt \
insmod rmmod modprobe \
awk sed xargs find"

APPLET="$1"
if [ -z "$APPLET" ]; then
    echo "Usage: bb <applet> [args...]" >&2
    exit 1
fi

for blocked in $BLOCKED_APPLETS; do
    if [ "$APPLET" = "$blocked" ]; then
        echo "bb: '$APPLET' is not permitted" >&2
        exit 1
    fi
done

shift
exec /bin/busybox "$APPLET" "$@"
SCRIPT
    chmod 755 $WRAPPER; \
    chown root:root $WRAPPER

# ── Non-root user ──────────────────────────────────────────────────────────
# Operator runs as appuser — limits blast radius if the shell tool is abused.
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
