from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.domain.models import Case, Notification, NotificationBatch
from app.repositories.base import Repository


@dataclass(frozen=True)
class _NotificationSeed:
    category: str
    severity: str
    case: Case
    due_date: date
    due_in_days: int


class NotificationService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def build_daily_digest(
        self,
        *,
        as_of: date | None = None,
        due_lookahead_days: int = 1,
        invoice_lookahead_days: int = 7,
        case_status: str | None = "in_progress",
        invoice_status: str | None = "pending",
    ) -> NotificationBatch:
        as_of_date = as_of or date.today()
        notifications: list[Notification] = []
        notifications.extend(
            self._build_due_notifications(
                as_of=as_of_date,
                lookahead_days=due_lookahead_days,
                status=case_status,
            )
        )
        notifications.extend(
            self._build_invoice_notifications(
                as_of=as_of_date,
                lookahead_days=invoice_lookahead_days,
                invoice_status=invoice_status,
            )
        )
        notifications.sort(key=_notification_sort_key)
        return NotificationBatch(
            as_of=as_of_date.isoformat(),
            due_lookahead_days=due_lookahead_days,
            invoice_lookahead_days=invoice_lookahead_days,
            notifications=notifications,
        )

    def _build_due_notifications(
        self,
        *,
        as_of: date,
        lookahead_days: int,
        status: str | None,
    ) -> list[Notification]:
        deadline = as_of + timedelta(days=max(0, lookahead_days))
        return [
            self._seed_to_notification(seed)
            for seed in self._collect_due_seed_cases(
                until_date=deadline.isoformat(),
                status=status,
                as_of=as_of,
                category="due_task",
            )
        ]

    def _build_invoice_notifications(
        self,
        *,
        as_of: date,
        lookahead_days: int,
        invoice_status: str | None,
    ) -> list[Notification]:
        deadline = as_of + timedelta(days=max(0, lookahead_days))
        return [
            self._seed_to_notification(seed)
            for seed in self._collect_invoice_seed_cases(
                due_before=deadline.isoformat(),
                invoice_status=invoice_status,
                as_of=as_of,
                category="invoice_reminder",
            )
        ]

    def _collect_due_seed_cases(
        self,
        *,
        until_date: str,
        status: str | None,
        as_of: date,
        category: str,
    ) -> list[_NotificationSeed]:
        cases = self._paginate_due_tasks(until_date=until_date, status=status)
        seeds: list[_NotificationSeed] = []
        for case in cases:
            if not case.due_date:
                continue
            due_date = date.fromisoformat(case.due_date)
            if due_date > date.fromisoformat(until_date):
                continue
            if due_date < as_of:
                severity = "overdue"
            elif due_date == as_of:
                severity = "urgent"
            else:
                severity = "warning"
            seeds.append(
                _NotificationSeed(
                    category=category,
                    severity=severity,
                    case=case,
                    due_date=due_date,
                    due_in_days=(due_date - as_of).days,
                )
            )
        return seeds

    def _collect_invoice_seed_cases(
        self,
        *,
        due_before: str,
        invoice_status: str | None,
        as_of: date,
        category: str,
    ) -> list[_NotificationSeed]:
        cases = self._paginate_invoices(invoice_status=invoice_status, due_before=due_before)
        seeds: list[_NotificationSeed] = []
        for case in cases:
            if not case.due_date:
                continue
            due_date = date.fromisoformat(case.due_date)
            if due_date < as_of:
                severity = "overdue"
            elif due_date == as_of:
                severity = "urgent"
            else:
                severity = "warning"
            seeds.append(
                _NotificationSeed(
                    category=category,
                    severity=severity,
                    case=case,
                    due_date=due_date,
                    due_in_days=(due_date - as_of).days,
                )
            )
        return seeds

    def _seed_to_notification(self, seed: _NotificationSeed) -> Notification:
        due_text = seed.due_date.isoformat()
        if seed.severity == "overdue":
            message = f"{seed.case.case_code} is overdue since {due_text}."
        elif seed.severity == "urgent":
            message = f"{seed.case.case_code} is due today ({due_text})."
        else:
            message = f"{seed.case.case_code} is due on {due_text}."
        return Notification(
            category=seed.category,
            severity=seed.severity,
            case_id=seed.case.id,
            case_code=seed.case.case_code,
            title=seed.case.title,
            message=message,
            due_date=due_text,
            due_in_days=seed.due_in_days,
        )

    def _paginate_due_tasks(self, *, until_date: str, status: str | None) -> list[Case]:
        return self._paginate(
            lambda limit, offset: self.repository.list_due_tasks(
                until_date=until_date,
                status=status,
                limit=limit,
                offset=offset,
            )
        )

    def _paginate_invoices(self, *, invoice_status: str | None, due_before: str) -> list[Case]:
        return self._paginate(
            lambda limit, offset: self.repository.list_invoices(
                invoice_status=invoice_status,
                due_before=due_before,
                limit=limit,
                offset=offset,
            )
        )

    def _paginate(self, fetch_page) -> list[Case]:  # noqa: ANN001
        items: list[Case] = []
        offset = 0
        while True:
            page = fetch_page(100, offset)
            items.extend(page)
            if len(page) < 100:
                break
            offset += 100
        return items


def _notification_sort_key(item: Notification) -> tuple[int, int, str]:
    severity_order = {"overdue": 0, "urgent": 1, "warning": 2}
    due_date = item.due_date or "9999-12-31"
    return (severity_order.get(item.severity, 99), 0 if item.category == "due_task" else 1, f"{due_date}:{item.case_code}")
