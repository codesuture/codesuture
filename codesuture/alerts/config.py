import os
import tomllib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CONFIG_FILE = '.codesuture.toml'

@dataclass
class WebhookConfig:
    enabled: bool = False
    url: str = ''
    format: str = 'raw'
    headers: Dict[str, str] = field(default_factory=dict)

@dataclass  
class FileAlertConfig:
    enabled: bool = True
    directory: str = '.codesuture_incidents/alerts'

@dataclass
class EscalationConfig:
    repeat_threshold: int = 5
    repeat_window_hours: int = 24
    escalate_to: str = 'CRITICAL'

@dataclass
class AlertConfig:
    enabled: bool = True
    file: FileAlertConfig = field(default_factory=FileAlertConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    routing: Dict[str, List[str]] = field(default_factory=lambda: {
        'CRITICAL': ['file', 'webhook'],
        'HIGH': ['file'],
        'MEDIUM': ['digest'],
        'LOW': ['digest'],
    })
    escalation: EscalationConfig = field(default_factory=EscalationConfig)

def load_config(config_path: str = None) -> AlertConfig:
    """Load config from .codesuture.toml or return defaults."""
    path = config_path or CONFIG_FILE
    config = AlertConfig()
    if not os.path.isfile(path):
        return config
    
    try:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
    except Exception:
        return config
    
    alerts = data.get('alerts', {})
    if not alerts:
        return config
    
    config.enabled = alerts.get('enabled', True)
    
    file_cfg = alerts.get('file', {})
    if file_cfg:
        config.file.enabled = file_cfg.get('enabled', True)
        config.file.directory = file_cfg.get('directory', config.file.directory)
    
    webhook_cfg = alerts.get('webhook', {})
    if webhook_cfg:
        config.webhook.enabled = webhook_cfg.get('enabled', False)
        config.webhook.url = webhook_cfg.get('url', '')
        config.webhook.format = webhook_cfg.get('format', 'raw')
        config.webhook.headers = webhook_cfg.get('headers', {})
    
    routing = alerts.get('routing', {})
    if routing:
        config.routing = {k.upper(): v for k, v in routing.items()}
    
    esc = alerts.get('escalation', {})
    if esc:
        config.escalation.repeat_threshold = esc.get('repeat_threshold', 5)
        config.escalation.repeat_window_hours = esc.get('repeat_window_hours', 24)
        config.escalation.escalate_to = esc.get('escalate_to', 'CRITICAL')
    
    return config
