"""Provider-specific adapters that map a real email provider's native inbound
webhook payload onto the existing, provider-agnostic `email_intake` pipeline
(`app.services.email_intake.process_attachment`). See `mailgun.py` for the
first shipped adapter (E1.6).
"""

from __future__ import annotations
