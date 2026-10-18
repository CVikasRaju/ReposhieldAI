"""Network connector: host allowlist, SSRF guard, payload scan and egress gate."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlsplit

import httpx

from backend.core.models import (
    ActionError,
    ActionRequest,
    ConnectorName,
    Decision,
    ExecResult,
    ExecutionPlan,
    Prepared,
    RuleVerdict,
)
from backend.engines.detectors import DetectContext, detect
from backend.policy.models import Policy

MAX_RESPONSE_BYTES = 1024 * 1024
HEADER_ALLOW = {"content-type", "content-length", "location", "server"}


class NetworkConnector:
    """Outbound HTTP through the egress gate."""

    name = "network"

    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.auto_confidence = policy.scoring.detectors.auto_mask_confidence

    async def prepare(self, req: ActionRequest) -> Prepared:
        method = str(req.params.get("method", "GET")).upper()
        url = str(req.params.get("url", req.target or ""))
        headers = req.params.get("headers") or {}
        body = str(req.params.get("body", "") or "")
        try:
            parsed = urlsplit(url)
        except ValueError:
            return self._blocked(method, url, "EGRESS_DENIED", "invalid URL")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return self._blocked(method, url, "EGRESS_DENIED", "invalid URL")
        host = parsed.hostname.lower()
        if not self.policy.egress_allows(host):
            return self._blocked(method, url, "EGRESS_DENIED", host)

        payload = "\n".join([url, json.dumps(headers, ensure_ascii=False), body])
        detections = detect(payload, DetectContext(target=f"host:{host}", ignore_below=0.4))
        if any(span.confidence >= self.auto_confidence for span in detections):
            return self._blocked(method, url, "EXFIL_DETECTED", host)

        if not await self._address_allowed(host):
            return self._blocked(method, url, "EGRESS_DENIED", host)

        if method in {"GET", "HEAD"}:
            decision = Decision.ALLOW
            tier = 4
        elif method in {item.upper() for item in self.policy.egress.hold_methods}:
            decision = Decision.HOLD
            tier = 4
        else:
            return self._blocked(method, url, "EGRESS_DENIED", host)

        return Prepared(
            connector=ConnectorName.NETWORK,
            action="http_request",
            target=host,
            display_target=url[:300],
            scope=f"host:{host}",
            tier=tier,
            read_operation=method in {"GET", "HEAD"},
            content=payload,
            content_kind="text",
            rule=RuleVerdict(
                decision=decision,
                reason_codes=["OUTBOUND_WRITE"] if decision == Decision.HOLD else [],
            ),
            meta={
                "host": host,
                "url": url,
                "method": method,
                "headers": headers,
                "body": body,
                "summary": f"{method} {url}"[:300],
                "tier_default": "ALLOW" if decision == Decision.ALLOW else "HOLD",
            },
        )

    def _blocked(self, method: str, url: str, reason: str, host: str) -> Prepared:
        return Prepared(
            connector=ConnectorName.NETWORK,
            action="http_request",
            target=host,
            display_target=url[:300],
            scope=f"host:{host}",
            tier=5,
            read_operation=False,
            content=None,
            rule=RuleVerdict(decision=Decision.BLOCK, reason_codes=[reason]),
            meta={"host": host, "url": url, "method": method, "summary": f"{method} {url}"[:300]},
        )

    async def _address_allowed(self, host: str) -> bool:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except socket.gaierror as exc:
            raise ActionError(f"Could not resolve host {host}.") from exc
        addresses = {
            info[4][0]
            for info in infos
            if info and len(info) >= 5 and info[4]
        }
        if not addresses:
            raise ActionError(f"Could not resolve host {host}.")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if self.policy.egress.allow_private_ranges:
                continue
            if (
                ip.is_loopback
                or ip.is_link_local
                or ip.is_private
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                return False
        return True

    async def execute(self, prepared: Prepared, plan: ExecutionPlan) -> ExecResult:
        if prepared.rule.decision == Decision.BLOCK:
            return ExecResult(ok=True, content=None)
        method = prepared.meta.get("method", "GET")
        url = prepared.meta.get("url", "")
        headers = prepared.meta.get("headers") or {}
        body = prepared.meta.get("body") or None
        try:
            async with httpx.AsyncClient(
                timeout=self.policy.egress.timeout_seconds, follow_redirects=False
            ) as client:
                response = await client.request(method, url, headers=headers, content=body)
        except httpx.HTTPError as exc:
            raise ActionError(f"Request failed: {type(exc).__name__}") from exc
        content = response.content[:MAX_RESPONSE_BYTES]
        truncated = len(response.content) > MAX_RESPONSE_BYTES
        payload = {
            "status": response.status_code,
            "headers": {
                key: value
                for key, value in response.headers.items()
                if key.lower() in HEADER_ALLOW
            },
            "body": content.decode("utf-8", errors="replace"),
            "truncated": truncated,
        }
        return ExecResult(content=json.dumps(payload, ensure_ascii=False), content_kind="text")
