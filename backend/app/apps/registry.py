"""The marketplace catalog: every app that can be installed."""

from app.apps.builtin import (
    daily_summary,
    gmail_intake,
    n8n_connector,
    payment_advice,
    razorpayx_payouts,
    reviewer_alerts,
    slack_alerts,
    vendor_followup,
)
from app.apps.manifest import AppManifest

APPS: dict[str, AppManifest] = {
    m.APP.id: m.APP
    for m in (
        gmail_intake,
        reviewer_alerts,
        vendor_followup,
        daily_summary,
        slack_alerts,
        razorpayx_payouts,
        payment_advice,
        n8n_connector,
    )
}
