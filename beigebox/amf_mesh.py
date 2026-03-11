"""
AMF Mesh Advertisement for BeigeBox.

Registers BeigeBox on the local AMF mesh at startup:
  1. mDNS/DNS-SD: _amf-agent._tcp.local  (RFC 6763)
     TXT record includes mcp= pointing to /mcp endpoint.
     The AMF coordinator will discover this, validate via DMZ watcher,
     admit to registry, and can route tasks here via MCP.

  2. NATS heartbeat (optional): publishes amf.discovery.agent.heartbeat
     as a CloudEvents v1.0 envelope if NATS is reachable.

Config (in config.yaml under amf_mesh: key):
  amf_mesh:
    enabled: true
    instance_name: "beigebox"   # mDNS service instance name
    host: "localhost"           # advertised host
    port: null                  # if null, uses server.port from config
    trust_domain: "local"
    nats_url: "nats://127.0.0.1:4222"  # set empty to disable NATS
    agent_id: null              # auto-generated UUID if null

Degrades gracefully: if zeroconf or nats-py are not installed, logs a
warning and skips the missing component.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_AMF_SERVICE_TYPE = "_amf-agent._tcp.local."
_PROTO_VERSION = "MCP/2024-11-05"


class AmfMeshAdvertiser:
    """
    Manages AMF mesh advertisement lifecycle for BeigeBox.
    Call start() in the FastAPI lifespan, stop() on shutdown.
    """

    def __init__(self, cfg: dict, tool_names: list[str]):
        amf_cfg = cfg.get("amf_mesh", {})
        server_cfg = cfg.get("server", {})

        self._enabled: bool = amf_cfg.get("enabled", False)
        self._instance: str = amf_cfg.get("instance_name", "beigebox")
        self._host: str = amf_cfg.get("host", "localhost")
        self._port: int = amf_cfg.get("port") or server_cfg.get("port", 8001)
        self._trust: str = amf_cfg.get("trust_domain", "local")
        self._nats_url: str = amf_cfg.get("nats_url", "nats://127.0.0.1:4222")
        self._agent_id: str = (
            amf_cfg.get("agent_id")
            or f"spiffe://local/beigebox/{_uuid.uuid4()}"
        )
        self._tool_names = tool_names

        self._endpoint = f"http://{self._host}:{self._port}"
        self._mcp_endpoint = f"{self._endpoint}/mcp"
        self._card_url = f"{self._endpoint}/.well-known/agent-card.json"

        self._zeroconf = None
        self._service_info = None
        self._nc = None  # NATS connection

    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self._enabled:
            logger.info("AMF mesh: disabled (set amf_mesh.enabled: true in config.yaml)")
            return

        logger.info(
            "AMF mesh: advertising as %s (MCP at %s, trust=%s)",
            self._instance, self._mcp_endpoint, self._trust,
        )

        await self._start_mdns()
        await self._start_nats()

    async def stop(self) -> None:
        if not self._enabled:
            return
        await self._publish_nats_event("amf.discovery.agent.heartbeat", {"status": "offline"})
        self._stop_mdns()
        await self._stop_nats()
        logger.info("AMF mesh: unregistered")

    # ------------------------------------------------------------------
    # mDNS
    # ------------------------------------------------------------------

    async def _start_mdns(self) -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf
            from zeroconf.asyncio import AsyncZeroconf
        except ImportError:
            logger.warning("AMF mesh: zeroconf not installed — mDNS advertisement disabled")
            return

        tags_csv = ",".join(self._tool_names)
        txt = {
            "id":     self._agent_id,
            "ep":     self._endpoint,
            "proto":  _PROTO_VERSION,
            "tags":   tags_csv,
            "td":     self._trust,
            "vis":    "local",
            "v":      "1.0.0",
            "status": "active",
            "card":   self._card_url,
            "mcp":    self._mcp_endpoint,
        }
        # Encode as b"key=value" bytes per DNS-SD TXT record convention
        txt_bytes = {k: v.encode() for k, v in txt.items()}

        try:
            # Resolve local IP
            local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            local_ip = "127.0.0.1"

        self._service_info = ServiceInfo(
            type_=_AMF_SERVICE_TYPE,
            name=f"{self._instance}.{_AMF_SERVICE_TYPE}",
            addresses=[socket.inet_aton(local_ip)],
            port=self._port,
            properties=txt_bytes,
            server=f"{self._instance}.local.",
        )

        try:
            self._zeroconf = AsyncZeroconf()
            await self._zeroconf.async_register_service(self._service_info)
            logger.info(
                "AMF mesh: mDNS registered %s._amf-agent._tcp.local on %s:%d",
                self._instance, local_ip, self._port,
            )
        except Exception as e:
            logger.warning("AMF mesh: mDNS registration failed: %s", e)
            self._zeroconf = None

    def _stop_mdns(self) -> None:
        if self._zeroconf and self._service_info:
            try:
                asyncio.get_event_loop().run_until_complete(
                    self._zeroconf.async_unregister_service(self._service_info)
                )
                asyncio.get_event_loop().run_until_complete(
                    self._zeroconf.async_close()
                )
            except Exception:
                pass
        self._zeroconf = None

    # ------------------------------------------------------------------
    # NATS
    # ------------------------------------------------------------------

    async def _start_nats(self) -> None:
        if not self._nats_url:
            return
        try:
            import nats as nats_lib
        except ImportError:
            logger.warning("AMF mesh: nats-py not installed — NATS heartbeat disabled")
            return

        try:
            self._nc = await nats_lib.connect(
                self._nats_url,
                connect_timeout=2,
                max_reconnect_attempts=3,
            )
            logger.info("AMF mesh: connected to NATS at %s", self._nats_url)
            await self._publish_nats_event(
                "amf.discovery.agent.heartbeat",
                {
                    "status":       "online",
                    "name":         self._instance,
                    "mcp_endpoint": self._mcp_endpoint,
                    "tools":        self._tool_names,
                },
            )
        except Exception as e:
            logger.info("AMF mesh: NATS unavailable (%s) — heartbeat disabled", e)
            self._nc = None

    async def _stop_nats(self) -> None:
        if self._nc:
            try:
                await self._nc.drain()
                await self._nc.close()
            except Exception:
                pass
        self._nc = None

    async def _publish_nats_event(self, event_type: str, payload: Any) -> None:
        if not self._nc:
            return
        evt = {
            "specversion":      "1.0",
            "id":               str(_uuid.uuid4()),
            "source":           self._agent_id,
            "type":             event_type,
            "time":             datetime.now(timezone.utc).isoformat(),
            "datacontenttype":  "application/json",
            "amfagentrole":     "specialist",
            "amfvisibility":    "local",
            "amfconfidence":    "1.0",
            "amfttl":           60,
            "amfschemaversion": "1.0.0",
            "amftrustdomain":   self._trust,
            "data": {
                "payload":      payload,
                "auth_context": {
                    "identity":     self._agent_id,
                    "trust_domain": self._trust,
                },
            },
        }
        try:
            await self._nc.publish(event_type, json.dumps(evt).encode())
        except Exception as e:
            logger.debug("AMF mesh: NATS publish failed: %s", e)
