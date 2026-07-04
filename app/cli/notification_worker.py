from __future__ import annotations

import asyncio
import argparse
import json
from datetime import date
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient

from app.api.main import build_line_webhook_report_payload, create_app
from app.domain.models import Notification, NotificationBatch
from app.services.notification_delivery import (
    AutoRoutingNotificationDelivery,
    ConsoleNotificationDelivery,
    DiscordWebhookNotificationDelivery,
    EmailSmtpNotificationDelivery,
    NotificationDelivery,
    LineMessagingApiNotificationDelivery,
    SlackWebhookNotificationDelivery,
    deliver_digest,
    render_notification_delivery_report_markdown,
    render_digest_markdown,
)
from app.services.notification_delivery_report import build_notification_delivery_report
from app.services.notifications import NotificationService


def _serialize(value):
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


_DELIVERY_CHOICES = ("none", "stdout", "auto", "discord-webhook", "slack-webhook", "line-push", "email-smtp")
_REPORT_DELIVERY_CHOICES = ("auto", "stdout", "discord-webhook", "slack-webhook", "line-push", "email-smtp")
_HELP_EXAMPLES = """Examples:
  python -m app.cli.notification_worker digest --dry-run --deliver-to auto
  python -m app.cli.notification_worker report --report-format markdown --report-granularity week
  python -m app.cli.notification_worker line-webhook-report --report-format markdown
  python -m app.cli.notification_worker line-webhook-alerts --deliver-to auto
"""


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def _add_digest_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as-of", dest="as_of", help="ISO date to evaluate against (default: today).")
    parser.add_argument("--due-lookahead-days", type=int, default=1)
    parser.add_argument("--invoice-lookahead-days", type=int, default=7)
    parser.add_argument("--case-status", default="in_progress")
    parser.add_argument("--invoice-status", default="pending")
    _add_delivery_arguments(parser)


def _add_delivery_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--deliver-to",
        choices=_DELIVERY_CHOICES,
        default="none",
        help="Optional delivery target for the rendered digest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the digest and planned delivery route without sending anything.",
    )
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=0,
        help="Retry failed deliveries this many additional times before giving up.",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=float,
        default=1.0,
        help="Delay between delivery retry attempts in seconds.",
    )


def _add_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, default=None, help="Optional file to write the output to.")


def _add_report_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--report-format",
        choices=("json", "markdown"),
        default="json",
        help="Format to use when the report command is enabled.",
    )
    parser.add_argument("--report-created-after", default=None)
    parser.add_argument("--report-created-before", default=None)
    parser.add_argument(
        "--report-deliver-to",
        choices=_REPORT_DELIVERY_CHOICES,
        default=None,
    )
    parser.add_argument("--report-granularity", choices=("day", "week", "month"), default="day")
    parser.add_argument("--report-recent-failures-limit", type=int, default=5)
    parser.add_argument("--report-recent-failures-offset", type=int, default=0)
    parser.add_argument("--report-limit-days", type=int, default=30)
    parser.add_argument("--report-failure-rate-threshold", type=float, default=0.25)
    parser.add_argument("--report-minimum-total-for-attention", type=int, default=5)


def _add_line_webhook_report_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--report-format",
        choices=("json", "markdown"),
        default="json",
        help="Format to use when rendering the LINE webhook backlog payload.",
    )
    parser.add_argument("--report-limit", type=int, default=20)
    parser.add_argument("--report-pending-backlog-threshold", type=int, default=5)


def _build_digest_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("digest", help="Generate and optionally deliver a daily notification digest.")
    _add_output_argument(parser)
    _add_digest_arguments(parser)
    return parser


def _build_report_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("report", help="Generate a notification delivery report from delivery history.")
    _add_output_argument(parser)
    _add_report_arguments(parser)
    return parser


def _build_line_webhook_report_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("line-webhook-report", help="Generate a LINE webhook backlog report.")
    _add_output_argument(parser)
    _add_line_webhook_report_arguments(parser)
    return parser


def _build_line_webhook_alerts_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("line-webhook-alerts", help="Generate or deliver LINE webhook backlog alerts.")
    _add_output_argument(parser)
    _add_delivery_arguments(parser)
    _add_line_webhook_report_arguments(parser)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a daily notification digest, a notification delivery report, or LINE webhook alerts.",
        epilog=_HELP_EXAMPLES,
        formatter_class=_HelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = False
    _build_digest_parser(subparsers)
    _build_report_parser(subparsers)
    _build_line_webhook_report_parser(subparsers)
    _build_line_webhook_alerts_parser(subparsers)
    return parser


def main() -> None:
    run()


def run(argv: list[str] | None = None, *, stream=None) -> int:
    args_list = list(argv) if argv is not None else sys.argv[1:]
    if not args_list:
        args_list = ["digest", *args_list]
    parser = build_parser()
    if args_list and args_list[0] in {"-h", "--help"}:
        parser.print_help(file=stream if stream is not None else sys.stdout)
        return 0
    if args_list and args_list[0].startswith("-"):
        args_list = ["digest", *args_list]
    args = parser.parse_args(args_list)
    app = create_app()
    output = stream if stream is not None else sys.stdout
    try:
        if args.command == "report":
            report = build_notification_delivery_report(
                app.state.repository,
                created_after=getattr(args, "report_created_after", None),
                created_before=getattr(args, "report_created_before", None),
                deliver_to=getattr(args, "report_deliver_to", None),
                granularity=getattr(args, "report_granularity", "day"),
                recent_failures_limit=getattr(args, "report_recent_failures_limit", 5),
                recent_failures_offset=getattr(args, "report_recent_failures_offset", 0),
                limit_days=getattr(args, "report_limit_days", 30),
                failure_rate_threshold=getattr(args, "report_failure_rate_threshold", 0.25),
                minimum_total_for_attention=getattr(args, "report_minimum_total_for_attention", 5),
            )
            if getattr(args, "report_format", "json") == "markdown":
                rendered = render_notification_delivery_report_markdown(report)
            else:
                rendered = json.dumps(_serialize(report), ensure_ascii=False, indent=2)
            if getattr(args, "output", None):
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered, encoding="utf-8")
            print(rendered, file=output)
            return 0
        if args.command == "line-webhook-report":
            report = _fetch_line_webhook_report(
                app,
                limit=getattr(args, "report_limit", 20),
                pending_backlog_threshold=getattr(args, "report_pending_backlog_threshold", 5),
            )
            if getattr(args, "report_format", "json") == "markdown":
                rendered = _render_line_webhook_report_markdown(report)
            else:
                rendered = json.dumps(_serialize(report), ensure_ascii=False, indent=2)
            if getattr(args, "output", None):
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered, encoding="utf-8")
            print(rendered, file=output)
            return 0
        if args.command == "line-webhook-alerts":
            report = _fetch_line_webhook_report(
                app,
                limit=getattr(args, "report_limit", 20),
                pending_backlog_threshold=getattr(args, "report_pending_backlog_threshold", 5),
            )
            alert_batch = _build_line_webhook_alert_batch(report)
            if getattr(args, "report_format", "json") == "markdown":
                rendered = _render_line_webhook_alerts_markdown(report, alert_batch)
            else:
                rendered = json.dumps(_serialize(alert_batch), ensure_ascii=False, indent=2)
            if getattr(args, "output", None):
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered, encoding="utf-8")
            print(rendered, file=output)
            if getattr(args, "dry_run", False):
                for line in _build_dry_run_preview(
                    app,
                    alert_batch,
                    args.deliver_to,
                    render_markdown=lambda batch: _render_line_webhook_alerts_markdown(report, batch),
                ):
                    print(line, file=output)
                return 0
            if args.deliver_to != "none":
                delivery = _build_delivery(app, args.deliver_to)
                try:
                    result, attempts = _deliver_with_retry(
                        alert_batch,
                        delivery,
                        retry_attempts=args.retry_attempts,
                        retry_delay_seconds=args.retry_delay_seconds,
                    )
                except Exception as exc:
                    _record_delivery_log(
                        app,
                        deliver_to=args.deliver_to,
                        digest=alert_batch,
                        destination=f"error:{args.deliver_to}",
                        delivered_count=0,
                        status="failed",
                        message="",
                        error_message=str(exc),
                        attempts=args.retry_attempts + 1,
                    )
                    raise
                _record_delivery_log(
                    app,
                    deliver_to=args.deliver_to,
                    digest=alert_batch,
                    destination=result.destination,
                    delivered_count=result.delivered_count,
                    status="success",
                    message=result.message,
                    error_message=None,
                    attempts=attempts,
                )
                if args.deliver_to != "stdout":
                    print(result.message, file=output)
            return 0

        service = NotificationService(app.state.repository)
        as_of = date.fromisoformat(args.as_of) if getattr(args, "as_of", None) else date.today()
        digest = service.build_daily_digest(
            as_of=as_of,
            due_lookahead_days=args.due_lookahead_days,
            invoice_lookahead_days=args.invoice_lookahead_days,
            case_status=args.case_status,
            invoice_status=args.invoice_status,
        )
        payload = _serialize(digest)
        if getattr(args, "output", None):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=output)
        if getattr(args, "dry_run", False):
            for line in _build_dry_run_preview(app, digest, args.deliver_to):
                print(line, file=output)
            return 0
        if args.deliver_to != "none":
            delivery = _build_delivery(app, args.deliver_to)
            try:
                result, attempts = _deliver_with_retry(
                    digest,
                    delivery,
                    retry_attempts=args.retry_attempts,
                    retry_delay_seconds=args.retry_delay_seconds,
                )
            except Exception as exc:
                _record_delivery_log(
                    app,
                    deliver_to=args.deliver_to,
                    digest=digest,
                    destination=f"error:{args.deliver_to}",
                    delivered_count=0,
                    status="failed",
                    message="",
                    error_message=str(exc),
                    attempts=args.retry_attempts + 1,
                )
                raise
            _record_delivery_log(
                app,
                deliver_to=args.deliver_to,
                digest=digest,
                destination=result.destination,
                delivered_count=result.delivered_count,
                status="success",
                message=result.message,
                error_message=None,
                attempts=attempts,
            )
            if args.deliver_to != "stdout":
                print(result.message, file=output)
        return 0
    finally:
        app.state.repository.close()


def _fetch_line_webhook_report(app, *, limit: int, pending_backlog_threshold: int):  # noqa: ANN001
    return build_line_webhook_report_payload(
        app.state.repository,
        limit=limit,
        pending_backlog_threshold=pending_backlog_threshold,
    )


def _build_line_webhook_alert_batch(report: dict[str, object]) -> NotificationBatch:
    requested_at = str(report.get("requested_at") or date.today().isoformat())
    summary = report.get("summary", {})
    notifications: list[Notification] = []
    if summary.get("needs_attention"):
        notifications.append(
            Notification(
                category="line_webhook_alert",
                severity="warning" if int(summary.get("pending_backlog_count", 0)) < 10 else "urgent",
                case_id=0,
                case_code="LINE-WEBHOOK",
                title="LINE webhook backlog",
                message=str(summary.get("attention_reason") or "LINE webhook backlog needs attention."),
                source="line-webhook-report",
            )
        )
    latest_pending = report.get("pending_backlog_latest")
    if isinstance(latest_pending, dict):
        notifications.append(
            Notification(
                category="line_webhook_alert",
                severity="warning",
                case_id=0,
                case_code="LINE-WEBHOOK",
                title=f"Pending LINE event {latest_pending.get('event_type') or 'unknown'}",
                message=f"Latest pending LINE webhook event: {latest_pending.get('event_summary') or latest_pending.get('message') or 'unknown'}",
                source="line-webhook-report",
            )
        )
    return NotificationBatch(
        as_of=requested_at,
        due_lookahead_days=0,
        invoice_lookahead_days=0,
        notifications=notifications,
    )


def _render_line_webhook_alerts_markdown(report: dict[str, object], alert_batch: NotificationBatch) -> str:
    summary = report.get("summary", {})
    lines = [
        "# O's flow LINE Webhook Alerts",
        f"- requested at: {report.get('requested_at', 'unknown')}",
        f"- pending backlog count: {summary.get('pending_backlog_count', 0)}",
        f"- needs attention: {summary.get('needs_attention', False)}",
    ]
    if summary.get("attention_reason"):
        lines.append(f"- attention reason: {summary['attention_reason']}")
    latest_pending = report.get("pending_backlog_latest")
    if latest_pending:
        lines.extend(
            [
                "",
                "## Latest Pending",
                f"- log_id: {latest_pending['id']}",
                f"- event_type: {latest_pending['event_type']}",
                f"- created_at: {latest_pending['created_at']}",
                f"- message: {latest_pending.get('message') or '-'}",
            ]
        )
    lines.extend(["", "## Alerts"])
    if alert_batch.notifications:
        for item in alert_batch.notifications:
            lines.append(
                f"- [{item.severity}] {item.case_code}: {item.title} - {item.message}"
            )
    else:
        lines.append("- No alerts.")
    return "\n".join(lines).strip()


def _render_line_webhook_report_markdown(report: dict[str, object]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# O's flow LINE Webhook Report",
        f"- pending backlog count: {summary.get('pending_backlog_count', 0)}",
        f"- needs attention: {summary.get('needs_attention', False)}",
    ]
    if summary.get("attention_reason"):
        lines.append(f"- attention reason: {summary['attention_reason']}")
    latest_pending = report.get("pending_backlog_latest")
    if latest_pending:
        lines.extend(
            [
                "",
                "## Latest Pending",
                f"- log_id: {latest_pending['id']}",
                f"- event_type: {latest_pending['event_type']}",
                f"- created_at: {latest_pending['created_at']}",
            ]
        )
    recent_events = report.get("recent_events", [])
    lines.extend(["", "## Recent Events"])
    if recent_events:
        for item in recent_events:
            lines.append(
                f"- {item.get('created_at')} | {item.get('operation_event_type')} | "
                f"{item.get('line_event_type')} | {item.get('message_type') or '-'}"
            )
    else:
        lines.append("- No recent events.")
    return "\n".join(lines).strip()


def _build_dry_run_preview(app, digest, deliver_to: str, *, render_markdown=render_digest_markdown):  # noqa: ANN001
    settings = app.state.settings
    summary = f"[dry-run] deliver-to={deliver_to}"
    if deliver_to == "none":
        return [summary, "[dry-run] no delivery will be attempted.", render_markdown(digest)]
    if deliver_to == "stdout":
        return [summary, "[dry-run] would render markdown to stdout.", render_markdown(digest)]
    if deliver_to == "auto":
        delivery = AutoRoutingNotificationDelivery(
            _build_available_deliveries(app),
            urgent_targets=settings.notification_auto_urgent_targets,
            warning_targets=settings.notification_auto_warning_targets,
        )
        routes = delivery.preview_routes(digest)
        if routes:
            return [
                summary,
                f"[dry-run] auto routing would use: {', '.join(routes)}",
                render_markdown(digest),
            ]
        return [summary, "[dry-run] auto routing has no configured delivery targets.", render_markdown(digest)]
    if deliver_to == "discord-webhook":
        if settings.notification_webhook_url:
            return [summary, "[dry-run] Discord webhook delivery is configured.", render_markdown(digest)]
        return [summary, "[dry-run] Discord webhook delivery is not configured.", render_markdown(digest)]
    if deliver_to == "slack-webhook":
        if settings.notification_slack_webhook_url:
            return [summary, "[dry-run] Slack webhook delivery is configured.", render_markdown(digest)]
        return [summary, "[dry-run] Slack webhook delivery is not configured.", render_markdown(digest)]
    if deliver_to == "line-push":
        if settings.notification_line_channel_access_token and settings.notification_line_recipient_ids:
            return [summary, "[dry-run] LINE push delivery is configured.", render_markdown(digest)]
        return [summary, "[dry-run] LINE push delivery is not configured.", render_markdown(digest)]
    if deliver_to == "email-smtp":
        if settings.notification_email_smtp_host and settings.notification_email_from and settings.notification_email_recipients:
            return [summary, "[dry-run] email delivery is configured.", render_markdown(digest)]
        return [summary, "[dry-run] email delivery is not configured.", render_markdown(digest)]
    return [summary, f"[dry-run] unsupported delivery target: {deliver_to}", render_markdown(digest)]


def _record_delivery_log(
    app,
    *,
    deliver_to: str,
    digest,
    destination: str,
    delivered_count: int,
    status: str,
    message: str,
    error_message: str | None,
    attempts: int,
):  # noqa: ANN001
    app.state.repository.record_notification_delivery(
        deliver_to=deliver_to,
        destination=destination,
        delivered_count=delivered_count,
        digest_as_of=digest.as_of,
        due_lookahead_days=digest.due_lookahead_days,
        invoice_lookahead_days=digest.invoice_lookahead_days,
        status=status,
        message=message,
        error_message=error_message,
        metadata_json={
            "notification_count": len(digest.notifications),
            "deliver_to": deliver_to,
            "attempts": attempts,
        },
    )


def _build_delivery(app, deliver_to: str):  # noqa: ANN001
    settings = app.state.settings
    if deliver_to == "stdout":
        return ConsoleNotificationDelivery()
    if deliver_to == "auto":
        return AutoRoutingNotificationDelivery(
            _build_available_deliveries(app),
            urgent_targets=settings.notification_auto_urgent_targets,
            warning_targets=settings.notification_auto_warning_targets,
        )
    if deliver_to == "discord-webhook":
        if not settings.notification_webhook_url:
            raise ValueError("NOTIFICATION_WEBHOOK_URL is required for discord-webhook delivery.")
        return DiscordWebhookNotificationDelivery(
            settings.notification_webhook_url,
            username=settings.notification_webhook_username,
            avatar_url=settings.notification_webhook_avatar_url,
        )
    if deliver_to == "slack-webhook":
        if not settings.notification_slack_webhook_url:
            raise ValueError("NOTIFICATION_SLACK_WEBHOOK_URL is required for slack-webhook delivery.")
        return SlackWebhookNotificationDelivery(settings.notification_slack_webhook_url)
    if deliver_to == "line-push":
        if not settings.notification_line_channel_access_token:
            raise ValueError("NOTIFICATION_LINE_CHANNEL_ACCESS_TOKEN is required for line-push delivery.")
        if not settings.notification_line_recipient_ids:
            raise ValueError("NOTIFICATION_LINE_RECIPIENT_IDS is required for line-push delivery.")
        return LineMessagingApiNotificationDelivery(
            api_base_url=settings.notification_line_api_base_url,
            channel_access_token=settings.notification_line_channel_access_token,
            recipient_ids=settings.notification_line_recipient_ids,
        )
    if deliver_to == "email-smtp":
        if not settings.notification_email_smtp_host:
            raise ValueError("NOTIFICATION_EMAIL_SMTP_HOST is required for email-smtp delivery.")
        if not settings.notification_email_from:
            raise ValueError("NOTIFICATION_EMAIL_FROM is required for email-smtp delivery.")
        if not settings.notification_email_recipients:
            raise ValueError("NOTIFICATION_EMAIL_RECIPIENTS is required for email-smtp delivery.")
        return EmailSmtpNotificationDelivery(
            smtp_host=settings.notification_email_smtp_host,
            smtp_port=settings.notification_email_smtp_port,
            username=settings.notification_email_smtp_username,
            password=settings.notification_email_smtp_password,
            use_tls=settings.notification_email_use_tls,
            from_address=settings.notification_email_from,
            recipients=settings.notification_email_recipients,
            subject_prefix=settings.notification_email_subject_prefix,
        )
    raise ValueError(f"Unsupported delivery target: {deliver_to}")


def _build_available_deliveries(app) -> dict[str, NotificationDelivery]:  # noqa: ANN001
    settings = app.state.settings
    deliveries: dict[str, NotificationDelivery] = {}
    if settings.notification_line_channel_access_token and settings.notification_line_recipient_ids:
        deliveries["line"] = _build_delivery(app, "line-push")
    if settings.notification_webhook_url:
        deliveries["discord"] = _build_delivery(app, "discord-webhook")
    if settings.notification_slack_webhook_url:
        deliveries["slack"] = _build_delivery(app, "slack-webhook")
    if (
        settings.notification_email_smtp_host
        and settings.notification_email_from
        and settings.notification_email_recipients
    ):
        deliveries["email"] = _build_delivery(app, "email-smtp")
    return deliveries


def _deliver_with_retry(
    digest,
    delivery: NotificationDelivery,
    *,
    retry_attempts: int,
    retry_delay_seconds: float,
):  # noqa: ANN001
    attempts = 0
    last_error: Exception | None = None
    max_attempts = max(1, retry_attempts + 1)
    while attempts < max_attempts:
        attempts += 1
        try:
            return asyncio.run(deliver_digest(digest, delivery)), attempts
        except Exception as exc:
            last_error = exc
            if attempts >= max_attempts:
                break
            time.sleep(max(0.0, retry_delay_seconds))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Delivery failed without an exception.")


if __name__ == "__main__":
    main()
