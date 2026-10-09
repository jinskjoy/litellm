import html
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing_extensions import ReadOnly, TypedDict

from litellm._logging import verbose_proxy_logger
from litellm.constants import (
    DAILY_EMAIL_SPEND_REPORT_JOB_ID,
    MONTHLY_EMAIL_SPEND_REPORT_JOB_ID,
)

LITELLM_LOGO_URL: Final = "https://litellm-listing.s3.amazonaws.com/litellm_logo.png"
LITELLM_SUPPORT_CONTACT: Final = "support@berri.ai"

SpendReportGroupBy = Literal["team", "team_key", "team_model", "team_key_model"]
SpendReportFrequency = Literal["daily", "monthly", "both"]
VALID_GROUP_BYS: Final[tuple[SpendReportGroupBy, ...]] = (
    "team",
    "team_key",
    "team_model",
    "team_key_model",
)


class SpendReportEmailSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(
        default=False,
        description="Whether email spend reports are enabled",
    )
    frequency: SpendReportFrequency = Field(
        default="daily",
        description="Report frequency: 'daily', 'monthly', or 'both'",
    )
    daily_send_time: str = Field(
        default="09:00",
        description="Daily spend report send time in HH:MM (24-hour UTC format)",
    )
    monthly_send_time: str = Field(
        default="09:00",
        description="Monthly spend report send time in HH:MM (24-hour UTC format), sent on the 1st of each month",
    )
    recipient_emails: list[str] = Field(
        default_factory=list,
        description="List of email addresses to receive the spend report",
    )
    group_by: list[SpendReportGroupBy] = Field(
        default_factory=lambda: ["team"],
        description="Groupings to include in cumulative reports: 'team', 'team_key', 'team_model', 'team_key_model'",
    )

    @field_validator("group_by", mode="before")
    @classmethod
    def validate_group_by(cls, v: object) -> list[str]:
        if isinstance(v, str):
            candidate_list: Final = [v]
        elif isinstance(v, (list, tuple)):
            candidate_list = [str(item) for item in v]
        else:
            candidate_list = ["team"]

        filtered: Final = [item for item in candidate_list if item in VALID_GROUP_BYS]
        return filtered if filtered else ["team"]


class SpendReportEmailSettingsResponse(BaseModel):
    values: dict[str, object]
    field_schema: dict[str, object]


class SpendReportEmailSendRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recipient_emails: list[str] | None = Field(
        default=None,
        description="Optional list of recipients. Defaults to configured recipient_emails",
    )
    frequency: Literal["daily", "monthly"] = Field(
        default="daily",
        description="Report frequency to generate: 'daily' or 'monthly'",
    )
    group_by: list[SpendReportGroupBy] | None = Field(
        default=None,
        description="Optional list of groupings to include in the report",
    )
    start_date: str | None = Field(
        default=None,
        description="Optional YYYY-MM-DD start date override",
    )
    end_date: str | None = Field(
        default=None,
        description="Optional YYYY-MM-DD end date override",
    )


class SpendReportSendResult(BaseModel):
    status: str
    message: str
    recipients: list[str]
    frequency: str
    start_date: str
    end_date: str
    total_spend: float
    total_requests: int


class TeamSpendRow(TypedDict):
    team: ReadOnly[str]
    total_spend: ReadOnly[float]
    request_count: ReadOnly[int]


class TeamKeySpendRow(TypedDict):
    team: ReadOnly[str]
    api_key: ReadOnly[str]
    total_spend: ReadOnly[float]
    request_count: ReadOnly[int]


class TeamModelSpendRow(TypedDict):
    team: ReadOnly[str]
    model: ReadOnly[str]
    total_spend: ReadOnly[float]
    request_count: ReadOnly[int]


class TeamKeyModelSpendRow(TypedDict):
    team: ReadOnly[str]
    api_key: ReadOnly[str]
    model: ReadOnly[str]
    total_spend: ReadOnly[float]
    request_count: ReadOnly[int]


TEAM_SPEND_SQL: Final = """
SELECT 
    COALESCE(t.team_alias, s.team_id, 'No Team') AS team,
    ROUND(SUM(s.spend)::numeric, 2) AS total_spend,
    COUNT(*) AS request_count
FROM "LiteLLM_SpendLogs" s
LEFT JOIN "LiteLLM_TeamTable" t ON s.team_id = t.team_id
WHERE s."startTime" >= ($1::timestamptz AT TIME ZONE 'UTC')
  AND s."startTime" < (($2::timestamptz + INTERVAL '1 day') AT TIME ZONE 'UTC')
GROUP BY COALESCE(t.team_alias, s.team_id, 'No Team')
HAVING SUM(s.spend) > 0
ORDER BY total_spend DESC
"""

TEAM_KEY_SPEND_SQL: Final = """
SELECT 
    COALESCE(t.team_alias, s.team_id, 'No Team') AS team,
    COALESCE(k.key_alias, k.key_name, NULLIF(s.api_key, ''), 'No Key') AS api_key,
    ROUND(SUM(s.spend)::numeric, 2) AS total_spend,
    COUNT(*) AS request_count
FROM "LiteLLM_SpendLogs" s
LEFT JOIN "LiteLLM_TeamTable" t ON s.team_id = t.team_id
LEFT JOIN "LiteLLM_VerificationToken" k ON s.api_key = k.token
WHERE s."startTime" >= ($1::timestamptz AT TIME ZONE 'UTC')
  AND s."startTime" < (($2::timestamptz + INTERVAL '1 day') AT TIME ZONE 'UTC')
GROUP BY 
    COALESCE(t.team_alias, s.team_id, 'No Team'),
    COALESCE(k.key_alias, k.key_name, NULLIF(s.api_key, ''), 'No Key')
HAVING SUM(s.spend) > 0
ORDER BY total_spend DESC
"""

TEAM_MODEL_SPEND_SQL: Final = """
SELECT 
    COALESCE(t.team_alias, s.team_id, 'No Team') AS team,
    COALESCE(NULLIF(s.model, ''), 'Unknown') AS model,
    ROUND(SUM(s.spend)::numeric, 2) AS total_spend,
    COUNT(*) AS request_count
FROM "LiteLLM_SpendLogs" s
LEFT JOIN "LiteLLM_TeamTable" t ON s.team_id = t.team_id
WHERE s."startTime" >= ($1::timestamptz AT TIME ZONE 'UTC')
  AND s."startTime" < (($2::timestamptz + INTERVAL '1 day') AT TIME ZONE 'UTC')
GROUP BY 
    COALESCE(t.team_alias, s.team_id, 'No Team'),
    COALESCE(NULLIF(s.model, ''), 'Unknown')
HAVING SUM(s.spend) > 0
ORDER BY total_spend DESC
"""

TEAM_KEY_MODEL_SPEND_SQL: Final = """
SELECT 
    COALESCE(t.team_alias, s.team_id, 'No Team') AS team,
    COALESCE(k.key_alias, k.key_name, NULLIF(s.api_key, ''), 'No Key') AS api_key,
    COALESCE(NULLIF(s.model, ''), 'Unknown') AS model,
    ROUND(SUM(s.spend)::numeric, 2) AS total_spend,
    COUNT(*) AS request_count
FROM "LiteLLM_SpendLogs" s
LEFT JOIN "LiteLLM_TeamTable" t ON s.team_id = t.team_id
LEFT JOIN "LiteLLM_VerificationToken" k ON s.api_key = k.token
WHERE s."startTime" >= ($1::timestamptz AT TIME ZONE 'UTC')
  AND s."startTime" < (($2::timestamptz + INTERVAL '1 day') AT TIME ZONE 'UTC')
GROUP BY 
    COALESCE(t.team_alias, s.team_id, 'No Team'),
    COALESCE(k.key_alias, k.key_name, NULLIF(s.api_key, ''), 'No Key'),
    COALESCE(NULLIF(s.model, ''), 'Unknown')
HAVING SUM(s.spend) > 0
ORDER BY total_spend DESC
"""

SPEND_REPORT_QUERIES: Final[Mapping[SpendReportGroupBy, str]] = {
    "team": TEAM_SPEND_SQL,
    "team_key": TEAM_KEY_SPEND_SQL,
    "team_model": TEAM_MODEL_SPEND_SQL,
    "team_key_model": TEAM_KEY_MODEL_SPEND_SQL,
}

GROUP_BY_TITLES: Final[Mapping[SpendReportGroupBy, str]] = {
    "team": "Spend by Team",
    "team_key": "Spend by Team and Key",
    "team_model": "Spend by Team and Model",
    "team_key_model": "Spend by Team, Key, and Model",
}

GROUP_BY_COLUMNS: Final[Mapping[SpendReportGroupBy, Sequence[tuple[str, str, str]]]] = {
    "team": (
        ("Team", "team", "left"),
        ("Total Spend", "total_spend", "right"),
        ("Requests", "request_count", "right"),
    ),
    "team_key": (
        ("Team", "team", "left"),
        ("Key / Token", "api_key", "left"),
        ("Total Spend", "total_spend", "right"),
        ("Requests", "request_count", "right"),
    ),
    "team_model": (
        ("Team", "team", "left"),
        ("Model", "model", "left"),
        ("Total Spend", "total_spend", "right"),
        ("Requests", "request_count", "right"),
    ),
    "team_key_model": (
        ("Team", "team", "left"),
        ("Key / Token", "api_key", "left"),
        ("Model", "model", "left"),
        ("Total Spend", "total_spend", "right"),
        ("Requests", "request_count", "right"),
    ),
}


def parse_send_time(time_str: str) -> tuple[int, int]:
    parts: Final = time_str.strip().split(":")
    if len(parts) != 2:
        return (9, 0)
    try:
        hour: Final = int(parts[0])
        minute: Final = int(parts[1])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour, minute)
    except ValueError:
        return (9, 0)
    return (9, 0)


def get_daily_report_date_range(ref_date: date | None = None) -> tuple[date, date]:
    current_date: Final = ref_date or datetime.now(timezone.utc).date()
    yesterday: Final = current_date - timedelta(days=1)
    return (yesterday, yesterday)


def get_monthly_report_date_range(ref_date: date | None = None) -> tuple[date, date]:
    current_date: Final = ref_date or datetime.now(timezone.utc).date()
    first_of_current: Final = current_date.replace(day=1)
    last_of_prev: Final = first_of_current - timedelta(days=1)
    first_of_prev: Final = last_of_prev.replace(day=1)
    return (first_of_prev, last_of_prev)


def get_spend_report_email_settings(
    config: Mapping[str, object] | None = None,
) -> SpendReportEmailSettings:
    if config is not None:
        raw_settings: Final = config.get("litellm_settings", {})
        if isinstance(raw_settings, dict):
            email_settings_data: Final = raw_settings.get("spend_report_email_settings")
            if isinstance(email_settings_data, dict):
                return SpendReportEmailSettings(**email_settings_data)

    import litellm

    in_memory_val: Final = getattr(litellm, "spend_report_email_settings", None)
    if isinstance(in_memory_val, dict):
        return SpendReportEmailSettings(**in_memory_val)
    if isinstance(in_memory_val, SpendReportEmailSettings):
        return in_memory_val

    return SpendReportEmailSettings()


async def _execute_raw_query(
    prisma_client: object,
    query: str,
    *args: object,
) -> Sequence[Mapping[str, object]]:
    client: Final = getattr(prisma_client, "db", prisma_client)
    raw_result: Final = await client.query_raw(query, *args)
    return cast(Sequence[Mapping[str, object]], raw_result)


async def fetch_grouped_spend_data(
    prisma_client: object,
    group_by: SpendReportGroupBy,
    start_date: str,
    end_date: str,
) -> Sequence[Mapping[str, object]]:
    sql_query: Final = SPEND_REPORT_QUERIES[group_by]
    raw_rows: Final = await _execute_raw_query(prisma_client, sql_query, start_date, end_date)
    sanitized_rows: Final[list[Mapping[str, object]]] = []
    for r in raw_rows:
        row_dict: Final[dict[str, object]] = dict(r)
        spend_val: Final = float(cast(float | int | str, row_dict.get("total_spend", 0.0)))
        if spend_val <= 0:
            continue
        row_dict["total_spend"] = round(spend_val, 2)
        row_dict["request_count"] = int(cast(float | int | str, row_dict.get("request_count", 0)))
        sanitized_rows.append(row_dict)
    return tuple(sanitized_rows)


def _render_table_html(
    group_by: SpendReportGroupBy,
    rows: Sequence[Mapping[str, object]],
) -> str:
    title: Final = GROUP_BY_TITLES[group_by]
    columns: Final = GROUP_BY_COLUMNS[group_by]
    header_cells_html: Final = "".join(
        f'<th style="padding: 10px 12px; font-weight: 600; color: #334155; text-align: {align};">{html.escape(label)}</th>'
        for label, _, align in columns
    )

    if not rows:
        colspan: Final = len(columns)
        tbody_html: Final = (
            f'<tr><td colspan="{colspan}" style="padding: 16px; text-align: center; color: #64748b;">'
            f"No spend recorded for this period</td></tr>"
        )
    else:
        row_html_list: Final[list[str]] = []
        for r in rows:
            cells_html: Final[list[str]] = []
            for _, field_key, align in columns:
                val: Final = r.get(field_key)
                if field_key == "total_spend":
                    formatted: Final = f"${float(cast(float | int | str, val or 0.0)):.2f}"
                elif field_key == "request_count":
                    formatted = f"{int(cast(float | int | str, val or 0)):,}"
                else:
                    formatted = str(val or "")
                escaped_val: Final = html.escape(formatted)
                cells_html.append(
                    f'<td style="padding: 9px 12px; color: #1e293b; text-align: {align};">{escaped_val}</td>'
                )
            row_html_list.append(f'<tr style="border-bottom: 1px solid #e2e8f0;">{"".join(cells_html)}</tr>')
        tbody_html = "".join(row_html_list)

    return f"""
    <div style="margin-bottom: 28px;">
        <h3 style="margin: 0 0 10px 0; font-size: 16px; font-weight: 600; color: #1e293b;">{title}</h3>
        <table style="width: 100%; border-collapse: collapse; font-size: 13px; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden;">
            <thead>
                <tr style="background-color: #f1f5f9; border-bottom: 2px solid #cbd5e1;">
                    {header_cells_html}
                </tr>
            </thead>
            <tbody>
                {tbody_html}
            </tbody>
        </table>
    </div>
    """


def build_spend_report_email_html(
    report_data: Mapping[SpendReportGroupBy, Sequence[Mapping[str, object]]],
    frequency: str,
    start_date: str,
    end_date: str,
) -> str:
    email_logo_url: Final = os.getenv("SMTP_SENDER_LOGO", os.getenv("EMAIL_LOGO_URL", LITELLM_LOGO_URL))
    email_support_contact: Final = os.getenv("EMAIL_SUPPORT_CONTACT", LITELLM_SUPPORT_CONTACT)

    overall_total_spend = 0.0
    overall_total_requests = 0
    for rows in report_data.values():
        if rows:
            overall_total_spend = sum(
                float(cast(float | int | str, r.get("total_spend", 0.0))) for r in rows
            )
            overall_total_requests = sum(
                int(cast(float | int | str, r.get("request_count", 0))) for r in rows
            )
            break

    frequency_title: Final = frequency.capitalize()
    tables_html: Final = "".join(
        _render_table_html(group_by, rows) for group_by, rows in report_data.items()
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 20px; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
        <div style="max-width: 680px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
            <div style="margin-bottom: 20px;">
                <img src="{email_logo_url}" alt="LiteLLM Logo" width="140" style="display: block;" />
            </div>

            <h2 style="margin: 0 0 6px 0; font-size: 20px; font-weight: 700; color: #0f172a;">
                LiteLLM {frequency_title} Spend Report
            </h2>
            <div style="font-size: 13px; color: #64748b; margin-bottom: 20px;">
                Period: <strong>{start_date}</strong> to <strong>{end_date}</strong>
            </div>

            <div style="margin-bottom: 24px; padding: 14px 16px; background-color: #f1f5f9; border-radius: 6px;">
                <div style="display: inline-block; width: 48%; vertical-align: top;">
                    <div style="font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600;">Total Spend</div>
                    <div style="font-size: 22px; font-weight: 700; color: #0f172a; margin-top: 4px;">${overall_total_spend:.2f}</div>
                </div>
                <div style="display: inline-block; width: 48%; vertical-align: top;">
                    <div style="font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600;">Total Requests</div>
                    <div style="font-size: 22px; font-weight: 700; color: #0f172a; margin-top: 4px;">{overall_total_requests:,}</div>
                </div>
            </div>

            {tables_html}

            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;" />
            <p style="font-size: 13px; color: #64748b; margin: 0 0 8px 0;">
                If you have any questions, please contact <a href="mailto:{email_support_contact}" style="color: #2563eb; text-decoration: none;">{email_support_contact}</a>
            </p>
            <p style="font-size: 13px; color: #64748b; margin: 0;">
                Best,<br />
                The LiteLLM Team
            </p>
        </div>
    </body>
    </html>
    """


async def send_spend_report_email(
    frequency: Literal["daily", "monthly"] = "daily",
    recipient_emails: Sequence[str] | None = None,
    group_by: Sequence[SpendReportGroupBy] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    prisma_client: object | None = None,
    email_sender: Callable[..., Awaitable[None]] | None = None,
) -> SpendReportSendResult:
    settings: Final = get_spend_report_email_settings()

    resolved_recipients: Final[Sequence[str]] = (
        recipient_emails if recipient_emails is not None and len(recipient_emails) > 0 else settings.recipient_emails
    )
    if not resolved_recipients:
        raise ValueError("No recipient emails configured for spend report email")

    if start_date is not None and end_date is not None:
        resolved_start_date: Final = start_date
        resolved_end_date: Final = end_date
    elif frequency == "daily":
        d_start, d_end = get_daily_report_date_range()
        resolved_start_date = d_start.strftime("%Y-%m-%d")
        resolved_end_date = d_end.strftime("%Y-%m-%d")
    else:
        m_start, m_end = get_monthly_report_date_range()
        resolved_start_date = m_start.strftime("%Y-%m-%d")
        resolved_end_date = m_end.strftime("%Y-%m-%d")

    resolved_groupings: Final[Sequence[SpendReportGroupBy]] = (
        group_by if group_by is not None and len(group_by) > 0 else tuple(settings.group_by)
    )

    active_prisma_client: Final = (
        prisma_client
        if prisma_client is not None
        else getattr(__import__("litellm.proxy.proxy_server", fromlist=["prisma_client"]), "prisma_client", None)
    )
    if active_prisma_client is None:
        raise RuntimeError("Prisma client is not connected")

    report_data: Final[dict[SpendReportGroupBy, Sequence[Mapping[str, object]]]] = {}
    for g in resolved_groupings:
        report_data[g] = await fetch_grouped_spend_data(
            active_prisma_client, g, resolved_start_date, resolved_end_date
        )

    html_content: Final = build_spend_report_email_html(
        report_data=report_data,
        frequency=frequency,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
    )

    if email_sender is not None:
        actual_sender = email_sender
    else:
        from litellm.proxy.utils import send_email

        actual_sender = send_email

    subject: Final = f"LiteLLM {frequency.capitalize()} Spend Report ({resolved_start_date} to {resolved_end_date})"
    for email_addr in resolved_recipients:
        await actual_sender(
            receiver_email=email_addr,
            subject=subject,
            html=html_content,
        )

    first_group_rows: Final = next(iter(report_data.values()), ())
    total_spend: Final = (
        sum(float(cast(float | int | str, r.get("total_spend", 0.0))) for r in first_group_rows)
        if first_group_rows
        else 0.0
    )
    total_requests: Final = (
        sum(int(cast(float | int | str, r.get("request_count", 0))) for r in first_group_rows)
        if first_group_rows
        else 0
    )

    return SpendReportSendResult(
        status="success",
        message=f"Spend report email sent to {len(resolved_recipients)} recipient(s)",
        recipients=list(resolved_recipients),
        frequency=frequency,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        total_spend=round(total_spend, 2),
        total_requests=total_requests,
    )


async def run_scheduled_email_spend_report(
    frequency: Literal["daily", "monthly"],
) -> None:
    from litellm.proxy.proxy_server import proxy_logging_obj

    job_id: Final = (
        DAILY_EMAIL_SPEND_REPORT_JOB_ID if frequency == "daily" else MONTHLY_EMAIL_SPEND_REPORT_JOB_ID
    )

    if proxy_logging_obj is not None:
        db_writer: Final = getattr(proxy_logging_obj, "db_spend_update_writer", None)
        pod_lock_manager: Final = getattr(db_writer, "pod_lock_manager", None) if db_writer is not None else None
        if pod_lock_manager is not None:
            has_lock: Final = await pod_lock_manager.acquire_lock(
                cronjob_id=job_id, ttl=3600, allow_reentrant=False
            )
            if not has_lock:
                verbose_proxy_logger.info("Email spend report lock already acquired by another pod: %s", job_id)
                return

    try:
        await send_spend_report_email(frequency=frequency)
    except Exception as e:
        verbose_proxy_logger.error("Failed to run scheduled email spend report (%s): %s", frequency, e)


def setup_email_spend_report_jobs(
    scheduler: object,
    settings: SpendReportEmailSettings | None = None,
) -> None:
    if scheduler is None:
        return

    add_job_fn: Final = getattr(scheduler, "add_job", None)
    remove_job_fn: Final = getattr(scheduler, "remove_job", None)
    if add_job_fn is None or remove_job_fn is None:
        return

    resolved_settings: Final = settings or get_spend_report_email_settings()

    async def _scheduled_daily() -> None:
        await run_scheduled_email_spend_report(frequency="daily")

    async def _scheduled_monthly() -> None:
        await run_scheduled_email_spend_report(frequency="monthly")

    if not resolved_settings.enabled or not resolved_settings.recipient_emails:
        try:
            remove_job_fn(DAILY_EMAIL_SPEND_REPORT_JOB_ID)
        except Exception:
            pass
        try:
            remove_job_fn(MONTHLY_EMAIL_SPEND_REPORT_JOB_ID)
        except Exception:
            pass
        return

    if resolved_settings.frequency in ("daily", "both"):
        d_hour, d_min = parse_send_time(resolved_settings.daily_send_time)
        add_job_fn(
            _scheduled_daily,
            "cron",
            hour=d_hour,
            minute=d_min,
            id=DAILY_EMAIL_SPEND_REPORT_JOB_ID,
            replace_existing=True,
        )
    else:
        try:
            remove_job_fn(DAILY_EMAIL_SPEND_REPORT_JOB_ID)
        except Exception:
            pass

    if resolved_settings.frequency in ("monthly", "both"):
        m_hour, m_min = parse_send_time(resolved_settings.monthly_send_time)
        add_job_fn(
            _scheduled_monthly,
            "cron",
            day=1,
            hour=m_hour,
            minute=m_min,
            id=MONTHLY_EMAIL_SPEND_REPORT_JOB_ID,
            replace_existing=True,
        )
    else:
        try:
            remove_job_fn(MONTHLY_EMAIL_SPEND_REPORT_JOB_ID)
        except Exception:
            pass
