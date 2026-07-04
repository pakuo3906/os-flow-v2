from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import httpx

from app.domain.models import Notification, NotificationBatch


@dataclass(frozen=True)
class DeliveryResult:
    delivered_count: int
    destination: str
    message: str


class NotificationDelivery(Protocol):
    async def send(self, digest: NotificationBatch) -> DeliveryResult:
        ...


def _digest_severity_rank(digest: NotificationBatch) -> int:
    severity_order = {"overdue": 0, "urgent": 1, "warning": 2}
    if not digest.notifications:
        return 99
    return min(severity_order.get(item.severity, 99) for item in digest.notifications)


def render_digest_markdown(digest: NotificationBatch) -> str:
    lines = [
        f"# O's flow Notification Digest ({digest.as_of})",
        f"- due lookahead: {digest.due_lookahead_days} day(s)",
        f"- invoice lookahead: {digest.invoice_lookahead_days} day(s)",
        "",
    ]
    if not digest.notifications:
        lines.append("No notifications.")
        return "\n".join(lines)

    current_category = None
    for item in digest.notifications:
        if item.category != current_category:
            current_category = item.category
            lines.extend(["", f"## {current_category.replace('_', ' ').title()}"])
        lines.append(_format_notification_line(item))
    return "\n".join(lines).strip()


def render_notification_delivery_report_markdown(report: dict[str, object]) -> str:
    summary = report.get("summary", {})
    trends = report.get("trends", {})
    alerts = report.get("alerts", {})
    lines = [
        "# O's flow Notification Delivery Report",
        f"- requested at: {report.get('requested_at', 'unknown')}",
        f"- granularity: {report.get('granularity', 'day')}",
        f"- deliver_to: {report.get('deliver_to') or 'all'}",
        f"- scope total: {report.get('scope_total', 0)}",
        "",
        "## Summary",
        f"- total: {summary.get('total', 0)}",
        f"- success: {summary.get('success_total', 0)}",
        f"- failed: {summary.get('failed_total', 0)}",
        f"- failure rate: {summary.get('failure_rate', 0.0)}",
        f"- needs attention: {summary.get('needs_attention', False)}",
    ]
    if summary.get("attention_reason"):
        lines.append(f"- attention reason: {summary['attention_reason']}")
    latest_delivery = summary.get("latest_delivery")
    latest_success = summary.get("latest_success")
    latest_failure = summary.get("latest_failure")
    if latest_delivery:
        lines.append(f"- latest delivery: {latest_delivery['created_at']} ({latest_delivery['status']})")
    if latest_success:
        lines.append(f"- latest success: {latest_success['created_at']} ({latest_success['digest_as_of']})")
    if latest_failure:
        lines.append(f"- latest failure: {latest_failure['created_at']} ({latest_failure['digest_as_of']})")

    attention_targets = summary.get("attention_targets", [])
    lines.extend(
        [
            f"- attention targets: {', '.join(attention_targets) if attention_targets else 'none'}",
            "",
            "## Trends",
        ]
    )
    for item in trends.get("trends", []):
        lines.append(
            f"- {item['period']}: total={item['total']}, success={item['success_total']}, "
            f"failed={item['failed_total']}, failure_rate={item['failure_rate']}"
        )
    if not trends.get("trends"):
        lines.append("- No trend data.")

    lines.extend(["", "## Alerts"])
    if alerts.get("alerts"):
        for item in alerts["alerts"]:
            lines.append(
                f"- {item['period']}: total={item['total']}, failure_rate={item['failure_rate']}, "
                f"needs_attention={item['needs_attention']}"
            )
    else:
        lines.append("- No alerts.")

    return "\n".join(lines).strip()


class ConsoleNotificationDelivery:
    async def send(self, digest: NotificationBatch) -> DeliveryResult:
        message = render_digest_markdown(digest)
        print(message)
        return DeliveryResult(delivered_count=len(digest.notifications), destination="stdout", message=message)


class DiscordWebhookNotificationDelivery:
    def __init__(
        self,
        webhook_url: str,
        *,
        username: str | None = None,
        avatar_url: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.username = username
        self.avatar_url = avatar_url
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def send(self, digest: NotificationBatch) -> DeliveryResult:
        message = render_digest_markdown(digest)
        chunks = _chunk_discord_message(message)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
            for chunk in chunks:
                payload = {"content": chunk}
                if self.username:
                    payload["username"] = self.username
                if self.avatar_url:
                    payload["avatar_url"] = self.avatar_url
                response = await client.post(self.webhook_url, json=payload)
                response.raise_for_status()
        return DeliveryResult(
            delivered_count=len(digest.notifications),
            destination="discord_webhook",
            message=message,
        )


class SlackWebhookNotificationDelivery:
    def __init__(
        self,
        webhook_url: str,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def send(self, digest: NotificationBatch) -> DeliveryResult:
        message = render_digest_markdown(digest)
        chunks = _chunk_discord_message(message, limit=3500)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
            for chunk in chunks:
                response = await client.post(self.webhook_url, json={"text": chunk})
                response.raise_for_status()
        return DeliveryResult(
            delivered_count=len(digest.notifications),
            destination="slack_webhook",
            message=message,
        )


class LineMessagingApiNotificationDelivery:
    def __init__(
        self,
        *,
        api_base_url: str,
        channel_access_token: str,
        recipient_ids: tuple[str, ...],
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.channel_access_token = channel_access_token
        self.recipient_ids = recipient_ids
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def send(self, digest: NotificationBatch) -> DeliveryResult:
        message = render_digest_markdown(digest)
        chunks = _chunk_discord_message(message, limit=4500)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
            for recipient_id in self.recipient_ids:
                for chunk in chunks:
                    response = await client.post(
                        f"{self.api_base_url}/v2/bot/message/push",
                        headers={
                            "Authorization": f"Bearer {self.channel_access_token}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "to": recipient_id,
                            "messages": [
                                {
                                    "type": "text",
                                    "text": chunk,
                                }
                            ],
                        },
                    )
                    response.raise_for_status()
        return DeliveryResult(
            delivered_count=len(digest.notifications),
            destination="line_messaging_api",
            message=message,
        )


class EmailSmtpNotificationDelivery:
    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int = 587,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        from_address: str,
        recipients: tuple[str, ...],
        subject_prefix: str = "O's flow",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.from_address = from_address
        self.recipients = recipients
        self.subject_prefix = subject_prefix
        self.timeout_seconds = timeout_seconds

    async def send(self, digest: NotificationBatch) -> DeliveryResult:
        message = render_digest_markdown(digest)
        email = EmailMessage()
        email["Subject"] = f"{self.subject_prefix} Notification Digest {digest.as_of}"
        email["From"] = self.from_address
        email["To"] = ", ".join(self.recipients)
        email.set_content(message)

        await asyncio.to_thread(self._send_message, email)
        return DeliveryResult(
            delivered_count=len(digest.notifications),
            destination="email_smtp",
            message=message,
        )

    def _send_message(self, email: EmailMessage) -> None:
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout_seconds) as client:
            if self.use_tls:
                client.starttls()
            if self.username:
                client.login(self.username, self.password or "")
            client.send_message(email)


class AutoRoutingNotificationDelivery:
    def __init__(
        self,
        deliveries: dict[str, NotificationDelivery],
        *,
        urgent_targets: tuple[str, ...] = ("line", "discord"),
        warning_targets: tuple[str, ...] = ("slack", "email"),
    ) -> None:
        self.deliveries = deliveries
        self.urgent_targets = urgent_targets
        self.warning_targets = warning_targets

    async def send(self, digest: NotificationBatch) -> DeliveryResult:
        route_names = self._select_routes(digest)
        if not route_names:
            raise ValueError("No notification deliveries are configured for auto routing.")

        delivered_count = 0
        messages: list[str] = []
        for route_name in route_names:
            delivery = self.deliveries.get(route_name)
            if delivery is None:
                continue
            result = await delivery.send(digest)
            delivered_count += result.delivered_count
            messages.append(f"{route_name}: {result.destination}")

        if not messages:
            raise ValueError("Auto routing could not find any configured delivery targets.")

        return DeliveryResult(
            delivered_count=delivered_count,
            destination="auto:" + ",".join(route_names),
            message=" | ".join(messages),
        )

    def preview_routes(self, digest: NotificationBatch) -> list[str]:
        return self._select_routes(digest)

    def _select_routes(self, digest: NotificationBatch) -> list[str]:
        highest = _digest_severity_rank(digest)
        if highest <= 1:
            preferred = list(self.urgent_targets)
            fallback = list(self.warning_targets)
        else:
            preferred = list(self.warning_targets)
            fallback = list(self.urgent_targets)

        routes = [name for name in preferred if name in self.deliveries]
        if routes:
            return routes
        return [name for name in fallback if name in self.deliveries]


async def deliver_digest(digest: NotificationBatch, delivery: NotificationDelivery) -> DeliveryResult:
    return await delivery.send(digest)


def _format_notification_line(item: Notification) -> str:
    due_bits = []
    if item.due_date:
        due_bits.append(item.due_date)
    if item.due_in_days is not None:
        due_bits.append(f"{item.due_in_days:+d} day(s)")
    due_text = f" ({', '.join(due_bits)})" if due_bits else ""
    return f"- [{item.severity}] {item.case_code}: {item.message}{due_text}"


def _chunk_discord_message(message: str, limit: int = 1900) -> list[str]:
    lines = message.splitlines()
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line) <= limit:
            current = line
        else:
            for part in _split_long_line(line, limit):
                chunks.append(part)
            current = ""
    if current:
        chunks.append(current)
    return chunks or [""]


def _split_long_line(line: str, limit: int) -> list[str]:
    return [line[i : i + limit] for i in range(0, len(line), limit)]
