import threading
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from collections import defaultdict
from codesuture.incidents.incident import IncidentRecord, Severity
from codesuture.alerts.config import AlertConfig, load_config
from codesuture.alerts.channels.file_channel import FileAlertChannel
from codesuture.alerts.channels.webhook_channel import WebhookChannel

_log = logging.getLogger(__name__)

class AlertRouter:
    def __init__(self, config: AlertConfig = None):
        self.config = config or load_config()
        self._lock = threading.Lock()
        self._batch_buffer: List[IncidentRecord] = []
        self._batch_timer: Optional[threading.Timer] = None
        self._escalation_tracker: dict = defaultdict(list)

        self._file_channel = None
        self._webhook_channel = None

        if self.config.file.enabled:
            self._file_channel = FileAlertChannel(self.config.file.directory)
        if self.config.webhook.enabled and self.config.webhook.url:
            self._webhook_channel = WebhookChannel(
                self.config.webhook.url,
                self.config.webhook.headers,
                self.config.webhook.format
            )

    def route(self, incident: IncidentRecord):
        """Route incident to appropriate channels based on severity."""
        if not self.config.enabled:
            return

        self._check_escalation(incident)

        severity = incident.severity.value
        channels = self.config.routing.get(severity, ['digest'])

        if 'file' in channels:
            self._send_file_alert(incident)
        if 'webhook' in channels:
            self._send_webhook_alert(incident)

        if severity == 'HIGH' and 'file' not in channels:
            self._add_to_batch(incident)

    def _send_file_alert(self, incident: IncidentRecord):
        if self._file_channel:
            try:
                self._file_channel.send(incident)
            except Exception as e:
                _log.error('File alert failed: %s', e)

    def _send_webhook_alert(self, incident: IncidentRecord):
        if self._webhook_channel:
            try:
                self._webhook_channel.send(incident)
            except Exception as e:
                _log.error('Webhook alert failed: %s', e)

    def _add_to_batch(self, incident: IncidentRecord):
        with self._lock:
            self._batch_buffer.append(incident)
            if self._batch_timer is None:
                self._batch_timer = threading.Timer(900.0, self._flush_batch)
                self._batch_timer.daemon = True
                self._batch_timer.start()

    def _flush_batch(self):
        with self._lock:
            buffer = self._batch_buffer[:]
            self._batch_buffer.clear()
            self._batch_timer = None

        for incident in buffer:
            self._send_file_alert(incident)

    def _check_escalation(self, incident: IncidentRecord):
        """Escalate if same function patched too many times."""
        func = incident.function
        if not func:
            return

        now = datetime.now(timezone.utc)
        window = timedelta(hours=self.config.escalation.repeat_window_hours)
        threshold = self.config.escalation.repeat_threshold

        with self._lock:
            timestamps = self._escalation_tracker[func]
            timestamps.append(now)

            cutoff = now - window
            timestamps[:] = [t for t in timestamps if t > cutoff]

            if len(timestamps) >= threshold:
                target_severity = Severity(self.config.escalation.escalate_to)
                if incident.severity != target_severity:
                    _log.warning('Escalating %s from %s to %s (patched %d times in %dh)',
                               func, incident.severity.value, target_severity.value,
                               len(timestamps), self.config.escalation.repeat_window_hours)
                    incident.severity = target_severity
                    incident.review_priority = 'URGENT'

    def flush(self):
        """Force-flush batched alerts."""
        self._flush_batch()

    def get_unread_alerts(self) -> str:
        """Get unread alerts summary."""
        if self._file_channel:
            return self._file_channel.get_unread()
        return 'No alert channel configured.'

    def dismiss_alert(self, alert_id: str) -> bool:
        if self._file_channel:
            return self._file_channel.dismiss(alert_id)
        return False

    def dismiss_all(self):
        if self._file_channel:
            self._file_channel.dismiss_all()
