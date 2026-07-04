from __future__ import annotations

from datetime import date


_VALID_GRANULARITIES = {"day", "week", "month"}


def _serialize(value):  # noqa: ANN001
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def _latest_notification_delivery(repository, **filters):  # noqa: ANN001
    deliveries = repository.list_notification_deliveries(limit=1, offset=0, **filters)
    if not deliveries:
        return None
    delivery = deliveries[0]
    return {
        "deliver_to": delivery.deliver_to,
        "destination": delivery.destination,
        "status": delivery.status,
        "created_at": delivery.created_at,
        "digest_as_of": delivery.digest_as_of,
        "delivered_count": delivery.delivered_count,
        "message": delivery.message,
    }


def build_notification_delivery_summary(
    repository,
    *,
    created_after: str | None = None,
    created_before: str | None = None,
    deliver_to: str | None = None,
    recent_failures_limit: int = 5,
    recent_failures_offset: int = 0,
    failure_rate_threshold: float = 0.25,
    minimum_total_for_attention: int = 5,
) -> dict[str, object]:
    deliver_to_modes = ("auto", "stdout", "discord-webhook", "slack-webhook", "line-push", "email-smtp")
    total = repository.count_notification_deliveries(
        deliver_to=deliver_to,
        created_after=created_after,
        created_before=created_before,
    )
    success_total = repository.count_notification_deliveries(
        deliver_to=deliver_to,
        status="success",
        created_after=created_after,
        created_before=created_before,
    )
    failed_total = repository.count_notification_deliveries(
        deliver_to=deliver_to,
        status="failed",
        created_after=created_after,
        created_before=created_before,
    )
    failure_rate = round((failed_total / total) if total else 0.0, 4)
    needs_attention = total >= minimum_total_for_attention and failure_rate >= failure_rate_threshold
    latest_delivery = _latest_notification_delivery(
        repository,
        deliver_to=deliver_to,
        created_after=created_after,
        created_before=created_before,
    )
    latest_success = _latest_notification_delivery(
        repository,
        deliver_to=deliver_to,
        status="success",
        created_after=created_after,
        created_before=created_before,
    )
    latest_failure = _latest_notification_delivery(
        repository,
        deliver_to=deliver_to,
        status="failed",
        created_after=created_after,
        created_before=created_before,
    )
    by_deliver_to = {}
    attention_targets: list[str] = []
    for mode in deliver_to_modes:
        mode_total = repository.count_notification_deliveries(
            deliver_to=mode,
            created_after=created_after,
            created_before=created_before,
        )
        mode_success_total = repository.count_notification_deliveries(
            deliver_to=mode,
            status="success",
            created_after=created_after,
            created_before=created_before,
        )
        mode_failed_total = repository.count_notification_deliveries(
            deliver_to=mode,
            status="failed",
            created_after=created_after,
            created_before=created_before,
        )
        mode_failure_rate = round((mode_failed_total / mode_total) if mode_total else 0.0, 4)
        mode_needs_attention = mode_total >= minimum_total_for_attention and mode_failure_rate >= failure_rate_threshold
        mode_latest_delivery = _latest_notification_delivery(
            repository,
            deliver_to=mode,
            created_after=created_after,
            created_before=created_before,
        )
        mode_latest_success = _latest_notification_delivery(
            repository,
            deliver_to=mode,
            status="success",
            created_after=created_after,
            created_before=created_before,
        )
        mode_latest_failure = _latest_notification_delivery(
            repository,
            deliver_to=mode,
            status="failed",
            created_after=created_after,
            created_before=created_before,
        )
        if mode_needs_attention:
            attention_targets.append(mode)
        by_deliver_to[mode] = {
            "total": mode_total,
            "success_total": mode_success_total,
            "failed_total": mode_failed_total,
            "failure_rate": mode_failure_rate,
            "needs_attention": mode_needs_attention,
            "attention_reason": (
                f"failure_rate {mode_failure_rate:.4f} is at or above threshold {failure_rate_threshold:.4f}"
                if mode_needs_attention
                else None
            ),
            "latest_delivery": mode_latest_delivery,
            "latest_success": mode_latest_success,
            "latest_failure": mode_latest_failure,
        }
    return {
        "total": total,
        "success_total": success_total,
        "failed_total": failed_total,
        "failure_rate": failure_rate,
        "needs_attention": needs_attention,
        "attention_reason": (
            f"failure_rate {failure_rate:.4f} is at or above threshold {failure_rate_threshold:.4f}"
            if needs_attention
            else None
        ),
        "attention_targets": attention_targets,
        "latest_delivery": latest_delivery,
        "latest_success": latest_success,
        "latest_failure": latest_failure,
        "by_deliver_to": by_deliver_to,
        "recent_failures": _serialize(
            repository.list_notification_deliveries(
                status="failed",
                created_after=created_after,
                created_before=created_before,
                limit=recent_failures_limit,
                offset=recent_failures_offset,
            )
        ),
    }


def build_notification_delivery_alerts(
    repository,
    *,
    created_after: str | None = None,
    created_before: str | None = None,
    deliver_to: str | None = None,
    granularity: str = "day",
    limit_days: int = 30,
    failure_rate_threshold: float = 0.25,
    minimum_total_for_attention: int = 5,
) -> dict[str, object]:
    if granularity not in _VALID_GRANULARITIES:
        raise ValueError("granularity must be one of: day, week, month")
    trends = repository.list_notification_delivery_trends(
        deliver_to=deliver_to,
        created_after=created_after,
        created_before=created_before,
        granularity=granularity,
        limit_days=limit_days,
        failure_rate_threshold=failure_rate_threshold,
        minimum_total_for_attention=minimum_total_for_attention,
    )
    alert_trends = [trend for trend in trends if trend.needs_attention]
    return {
        "granularity": granularity,
        "alert_total": len(alert_trends),
        "alerts": _serialize(alert_trends),
        "deliver_to": deliver_to,
    }


def build_notification_delivery_report(
    repository,
    *,
    created_after: str | None = None,
    created_before: str | None = None,
    deliver_to: str | None = None,
    granularity: str = "day",
    recent_failures_limit: int = 5,
    recent_failures_offset: int = 0,
    limit_days: int = 30,
    failure_rate_threshold: float = 0.25,
    minimum_total_for_attention: int = 5,
) -> dict[str, object]:
    if granularity not in _VALID_GRANULARITIES:
        raise ValueError("granularity must be one of: day, week, month")
    summary = build_notification_delivery_summary(
        repository,
        created_after=created_after,
        created_before=created_before,
        deliver_to=deliver_to,
        recent_failures_limit=recent_failures_limit,
        recent_failures_offset=recent_failures_offset,
        failure_rate_threshold=failure_rate_threshold,
        minimum_total_for_attention=minimum_total_for_attention,
    )
    trends = {
        "granularity": granularity,
        "trends": _serialize(
            repository.list_notification_delivery_trends(
                deliver_to=deliver_to,
                created_after=created_after,
                created_before=created_before,
                granularity=granularity,
                limit_days=limit_days,
                failure_rate_threshold=failure_rate_threshold,
                minimum_total_for_attention=minimum_total_for_attention,
            )
        ),
    }
    alerts = build_notification_delivery_alerts(
        repository,
        created_after=created_after,
        created_before=created_before,
        deliver_to=deliver_to,
        granularity=granularity,
        limit_days=limit_days,
        failure_rate_threshold=failure_rate_threshold,
        minimum_total_for_attention=minimum_total_for_attention,
    )
    return {
        "summary": summary,
        "trends": trends,
        "alerts": alerts,
        "deliver_to": deliver_to,
        "granularity": granularity,
        "requested_at": date.today().isoformat(),
        "scope_total": repository.count_notification_deliveries(
            deliver_to=deliver_to,
            created_after=created_after,
            created_before=created_before,
        ),
        "attention_targets": summary["attention_targets"],
        "latest_delivery": summary["latest_delivery"],
        "latest_success": summary["latest_success"],
        "latest_failure": summary["latest_failure"],
        "needs_attention": summary["needs_attention"],
        "attention_reason": summary["attention_reason"],
        "alert_total": alerts["alert_total"],
    }
