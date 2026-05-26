import sys
import io
import argparse
from codesuture.tracer import install, uninstall, _install_trace_on_all_threads


def _ensure_utf8_stdout():
    """Reconfigure stdout/stderr to handle Unicode on Windows (cp1252).

    Uses a two-layer approach:
    1. Try reconfigure() — works for most CPython TextIOWrapper instances.
    2. If stdout encoding is still not UTF-8 and a raw .buffer is available,
       replace sys.stdout with a fresh TextIOWrapper(errors='replace') so
       that print() never raises UnicodeEncodeError regardless of terminal.
    """
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name)
        # Layer 1: reconfigure if supported
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
                continue  # Success — encoding changed, move to next stream
            except Exception:
                pass
        # Layer 2: wrap the raw buffer directly
        if hasattr(stream, 'buffer'):
            try:
                wrapped = io.TextIOWrapper(
                    stream.buffer,
                    encoding='utf-8',
                    errors='replace',
                    line_buffering=stream.line_buffering
                    if hasattr(stream, 'line_buffering') else True,
                )
                setattr(sys, stream_name, wrapped)
            except Exception:
                pass  # Can't fix it — best effort


def main():
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(prog='codesuture',
                                     description='Runtime Python bytecode patcher with self-healing re-execution')
    parser.add_argument('--version', action='version', version='codesuture 1.0.0')
    sub = parser.add_subparsers(dest='command', required=True)

    run_parser = sub.add_parser('run', help='Run a script with live patching')
    run_parser.add_argument('script', help='Target script to run')
    run_parser.add_argument('--dry-run', action='store_true', help='Show what would be patched without applying')
    run_parser.add_argument('--log', metavar='FILE', help='Append patch events (JSON lines) to FILE')
    run_parser.add_argument('--retries', type=int, default=3, metavar='N', help='Max patching attempts (default: 3)')
    run_parser.add_argument('--self-test', action='store_true', help='Corrupt the engine to test self-healing')
    run_parser.add_argument('--autonomous', action='store_true', help='Enable autonomous LLM bug-fixing')
    run_parser.add_argument('--verbose', action='store_true', help='Show detailed debug output')
    run_parser.add_argument('--shadow', action='store_true', help='Warn if patched functions return sentinel values')
    run_parser.add_argument('--ttl', type=int, default=7, metavar='DAYS', help='Patch TTL in days (default: 7)')
    run_parser.add_argument('--silent', action='store_true',
                            help='Suppress exception output for healed crashes (default: show summary)')

    sub.add_parser('audit', help='Show all active patches')

    rb_parser = sub.add_parser('rollback', help='Remove persisted patches')
    rb_parser.add_argument('function_name', nargs='?', default=None, help='Function name to roll back')
    rb_parser.add_argument('--all', action='store_true', dest='rollback_all', help='Remove ALL patches + fingerprints')
    rb_parser.add_argument('--dry-run', action='store_true', dest='rollback_dry_run', help='List what would be removed')

    watch_parser = sub.add_parser('watch', help='Watch and auto-restart a script with live patching')
    watch_parser.add_argument('script', help='Target script to watch')
    watch_parser.add_argument('--max-restarts', type=int, default=10, metavar='N',
                              help='Maximum number of restarts (default: 10)')
    watch_parser.add_argument('--shadow', action='store_true', help='Enable shadow mode warnings')
    watch_parser.add_argument('--verbose', action='store_true', help='Show detailed debug output')

    explain_parser = sub.add_parser('explain', help='Show detailed explanation of active patches')
    explain_parser.add_argument('func_name', nargs='?', default=None, help='Function name to explain')

    # --- Phase 3: Incident Intelligence ---
    inc_parser = sub.add_parser('incidents', help='Show incident log')
    inc_parser.add_argument('--since', metavar='PERIOD', default='1d',
                            help='Time window as number of days, e.g. 1d, 2d, 7d, 30d (default: 1d)')
    inc_parser.add_argument('--severity', choices=['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'],
                            help='Filter by severity')
    inc_parser.add_argument('--function', metavar='NAME', help='Filter by function name')
    inc_parser.add_argument('--json', action='store_true', dest='json_output',
                            help='Output raw JSON instead of table')

    digest_parser = sub.add_parser('digest', help='Generate incident digest report')
    digest_parser.add_argument('--weekly', action='store_true', help='Generate weekly digest instead of daily')
    digest_parser.add_argument('--export', metavar='FILE', help='Export digest to file')

    # --- Phase 4: Alert System ---
    alert_parser = sub.add_parser('alerts', help='Manage alerts')
    alert_sub = alert_parser.add_subparsers(dest='alert_action')
    alert_sub.add_parser('show', help='Show unread alerts (default)')
    alert_sub.add_parser('all', help='Show all alerts')
    dismiss_parser = alert_sub.add_parser('dismiss', help='Dismiss an alert')
    dismiss_parser.add_argument('alert_id', nargs='?', default=None, help='Alert ID to dismiss')
    dismiss_parser.add_argument('--all', action='store_true', dest='dismiss_all', help='Dismiss all alerts')
    alert_sub.add_parser('config', help='Show current alert configuration')
    alert_sub.add_parser('test', help='Send a test alert to verify channels')

    # --- Phase 5: Fix Suggestions ---
    suggest_parser = sub.add_parser('suggest', help='Show fix suggestions for active patches')
    suggest_parser.add_argument('func_name', nargs='?', default=None,
                                help='Function name to show suggestion for')
    suggest_parser.add_argument('--diff', action='store_true',
                                help='Show all suggestions as unified diffs')
    suggest_parser.add_argument('--json', action='store_true', dest='suggest_json',
                                help='Output suggestions as JSON')

    # --- Phase 8: Lifecycle & Metrics ---
    lifecycle_parser = sub.add_parser('lifecycle', help='Manage patch lifecycle')
    lifecycle_sub = lifecycle_parser.add_subparsers(dest='lifecycle_action')
    lifecycle_sub.add_parser('show', help='Show all patch lifecycle states')
    lifecycle_sub.add_parser('stale', help='Show stale patches (persisted >5 days without fix)')
    lifecycle_sub.add_parser('expired', help='Show expired patches')
    lifecycle_sub.add_parser('summary', help='Summary counts by state')
    lf_fix = lifecycle_sub.add_parser('fix', help='Mark a function as permanently fixed')
    lf_fix.add_argument('func_name', help='Function name to mark as fixed')

    metrics_parser = sub.add_parser('metrics', help='Export CodeSuture metrics')
    metrics_parser.add_argument('--format', choices=['prometheus', 'json'], default='prometheus',
                                dest='metrics_format', help='Output format')

    args = parser.parse_args()

    if args.command == 'audit':
        from codesuture.audit import run_audit
        run_audit()
        return

    if args.command == 'rollback':
        from codesuture.rollback import rollback_function, rollback_all, rollback_dry_run
        if args.rollback_dry_run:
            rollback_dry_run()
        elif args.rollback_all:
            rollback_all()
        elif args.function_name:
            rollback_function(args.function_name)
        else:
            print("[CodeSuture] Usage: codesuture rollback <function_name> | --all | --dry-run")
        return

    if args.command == 'watch':
        from codesuture.watcher import watch
        exit_code = watch(
            args.script,
            max_restarts=args.max_restarts,
            shadow=args.shadow,
            verbose=args.verbose,
        )
        sys.exit(exit_code)

    if args.command == 'explain':
        from codesuture.explain import run_explain
        run_explain(args.func_name)
        return

    if args.command == 'incidents':
        _handle_incidents(args)
        return

    if args.command == 'digest':
        _handle_digest(args)
        return

    if args.command == 'alerts':
        _handle_alerts(args)
        return

    if args.command == 'suggest':
        _handle_suggest(args)
        return

    if args.command == 'lifecycle':
        _handle_lifecycle(args)
        return

    if args.command == 'metrics':
        _handle_metrics(args)
        return

    if getattr(args, 'autonomous', False):
        try:
            import llama_cpp
        except ImportError:
            print("Autonomous mode requires llama-cpp-python. Install with: pip install codesuture[autonomous]")
            sys.exit(1)

    if args.command == 'run':
        from codesuture.persistence import install_import_hook, make_persisted_patch_globals

        install_import_hook()

        if getattr(args, 'self_test', False):
            import codesuture.pattern_matcher as pm
            print("[CodeSuture] SELF-TEST: corrupting _infer_default -> None")
            pm._infer_default = None
        tracer = None
        try:
            with open(args.script, 'r', encoding='utf-8') as f:
                source = f.read()
            code = compile(source, args.script, 'exec')

            tracer = install(dry_run=args.dry_run, log_file=args.log,
                             max_retries=args.retries,
                             autonomous=getattr(args, 'autonomous', False),
                             script_path=args.script, verbose=args.verbose,
                             shadow=args.shadow, ttl=args.ttl,
                             silent=args.silent)
            max_runs = args.retries + 1
            for run in range(max_runs):
                patched_before = tracer.stats['patched']
                tracer._handled_exc_ids.clear()
                try:
                    _install_trace_on_all_threads(tracer)
                    globs = make_persisted_patch_globals(
                        "__main__",
                        {'__name__': '__main__', '__file__': args.script},
                    )
                    exec(code, globs)
                    break
                except Exception as e:
                    sys.settrace(None)
                    new_patches = tracer.stats['patched'] - patched_before
                    if new_patches > 0 and run < max_runs - 1:
                        print(f"[CodeSuture] Re-executing after {new_patches} patch(es)...")
                        continue
                    else:
                        print(f"[CodeSuture] Script exited with: {e}")
                        break
        finally:
            uninstall()
            if tracer is not None:
                tracer.report()


def _parse_since(since_str: str):
    """Parse '1d', '7d', '30d' into a datetime."""
    from datetime import datetime, timezone, timedelta
    since_str = since_str.strip().lower()
    if since_str.endswith('d'):
        try:
            days = int(since_str[:-1])
        except ValueError:
            days = 1
    else:
        days = 1
    return datetime.now(timezone.utc) - timedelta(days=days)


def _handle_incidents(args):
    """Handle the 'incidents' command."""
    import json as _json
    from datetime import datetime, timezone
    from codesuture.incidents.incident_log import IncidentLogger
    from codesuture.incidents.incident import Severity

    logger = IncidentLogger()
    since = _parse_since(args.since)

    severity_filter = Severity(args.severity) if args.severity else None
    incidents = logger.get_incidents(
        since=since,
        severity=severity_filter,
        function=args.function,
    )

    if not incidents:
        print("[CodeSuture] No incidents found for the specified period.")
        return

    if args.json_output:
        for inc in incidents:
            print(_json.dumps(inc.to_dict(), default=str))
        return

    # Table output
    print()
    print(f"  CodeSuture Incidents (since {since.strftime('%Y-%m-%d %H:%M')} UTC)")
    print()
    print(f"  {'Time':<20} {'Severity':<10} {'Function':<25} {'Guard':<20} {'Target':<15} {'Status':<10}")
    print(f"  {'─'*20} {'─'*10} {'─'*25} {'─'*20} {'─'*15} {'─'*10}")

    for inc in incidents:
        ts = inc.timestamp[:19] if len(inc.timestamp) >= 19 else inc.timestamp
        print(f"  {ts:<20} {inc.severity.value:<10} {inc.function:<25} "
              f"{inc.guard_type:<20} {inc.target_variable:<15} {inc.status.value:<10}")

    print()
    print(f"  Total: {len(incidents)} incident(s)")
    counts = {}
    for inc in incidents:
        counts[inc.severity.value] = counts.get(inc.severity.value, 0) + 1
    parts = [f"{sev}: {cnt}" for sev, cnt in sorted(counts.items())]
    if parts:
        print(f"  Breakdown: {' | '.join(parts)}")
    print()


def _handle_digest(args):
    """Handle the 'digest' command."""
    from codesuture.incidents.incident_log import IncidentLogger
    from codesuture.incidents.digest import DigestGenerator

    logger = IncidentLogger()
    generator = DigestGenerator(logger)

    if args.weekly:
        content = generator.generate_weekly()
    else:
        content = generator.generate_daily()

    if args.export:
        with open(args.export, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"[CodeSuture] Digest exported to {args.export}")
    else:
        print(content)


def _handle_alerts(args):
    """Handle the 'alerts' command."""
    from codesuture.alerts.config import load_config
    from codesuture.alerts.router import AlertRouter

    config = load_config()
    router = AlertRouter(config)

    action = args.alert_action or 'show'

    if action == 'show':
        print()
        print("  CodeSuture — Unread Alerts")
        print()
        print(router.get_unread_alerts())

    elif action == 'all':
        import os
        alert_dir = config.file.directory
        if not os.path.isdir(alert_dir):
            print("[CodeSuture] No alerts directory found.")
            return
        files = sorted(f for f in os.listdir(alert_dir) if f.endswith('.md') and f != 'unread.md')
        if not files:
            print("[CodeSuture] No alert files found.")
            return
        for fname in files:
            fpath = os.path.join(alert_dir, fname)
            with open(fpath, 'r', encoding='utf-8') as f:
                print(f.read())
            print("---")

    elif action == 'dismiss':
        if getattr(args, 'dismiss_all', False):
            router.dismiss_all()
            print("[CodeSuture] All alerts dismissed.")
        elif args.alert_id:
            if router.dismiss_alert(args.alert_id):
                print(f"[CodeSuture] Alert {args.alert_id} dismissed.")
            else:
                print(f"[CodeSuture] Alert {args.alert_id} not found.")
        else:
            print("[CodeSuture] Usage: codesuture alerts dismiss <id> | --all")

    elif action == 'config':
        print()
        print("  CodeSuture Alert Configuration")
        print()
        print(f"  Enabled:     {config.enabled}")
        print(f"  File alerts: {config.file.enabled} → {config.file.directory}")
        print(f"  Webhook:     {config.webhook.enabled}" +
              (f" → {config.webhook.url}" if config.webhook.url else ""))
        print(f"  Routing:")
        for sev, channels in config.routing.items():
            print(f"    {sev}: {', '.join(channels)}")
        print(f"  Escalation:  {config.escalation.repeat_threshold}× in "
              f"{config.escalation.repeat_window_hours}h → {config.escalation.escalate_to}")
        print()

    elif action == 'test':
        from codesuture.incidents.incident import IncidentRecord, Severity, IncidentStatus
        test_incident = IncidentRecord(
            exception_type='TestError',
            exception_message='This is a test alert from CodeSuture',
            function='test_function',
            file_path='test.py',
            line_number=1,
            severity=Severity.HIGH,
            status=IncidentStatus.PATCHED,
            guard_type='null_guard',
            target_variable='test_var',
            default_value='',
        )
        router.route(test_incident)
        print("[CodeSuture] Test alert sent. Check your configured channels.")


def _handle_suggest(args):
    """Handle the 'suggest' command — show source-level fix suggestions."""
    import json as _json
    from codesuture.incidents.incident_log import IncidentLogger
    from codesuture.suggest import generate_suggestion, format_suggestion

    logger = IncidentLogger()

    # Get recent incidents
    from datetime import datetime, timezone, timedelta
    since = datetime.now(timezone.utc) - timedelta(days=30)
    incidents = logger.get_incidents(since=since)

    if not incidents:
        print("[CodeSuture] No incidents found. Run a script first to generate incidents.")
        return

    # Filter by function if specified
    if args.func_name:
        incidents = [i for i in incidents if args.func_name.lower() in i.function.lower()]
        if not incidents:
            print(f"[CodeSuture] No incidents found for '{args.func_name}'.")
            return

    # Deduplicate by function+guard_type (show latest)
    seen = {}
    for inc in incidents:
        key = (inc.function, inc.guard_type)
        seen[key] = inc  # Keep last occurrence
    unique_incidents = list(seen.values())

    suggestions = []
    for inc in unique_incidents:
        suggestion = generate_suggestion(inc)
        if suggestion:
            suggestions.append(suggestion)

    if not suggestions:
        print("[CodeSuture] No fix suggestions could be generated for recent incidents.")
        return

    if args.suggest_json:
        for s in suggestions:
            print(_json.dumps({
                'function': s.function_name,
                'file': s.file_path,
                'line': s.line_number,
                'guard': s.guard_type,
                'target': s.target_variable,
                'confidence': s.confidence,
                'original': s.original_line,
                'suggested': s.suggested_line,
                'explanation': s.explanation,
                'diff': s.diff,
            }))
        return

    if args.diff:
        for s in suggestions:
            print(s.diff)
            print()
        return

    # Default: formatted output
    print()
    print(f"  CodeSuture Fix Suggestions ({len(suggestions)} found)")
    print()
    for i, s in enumerate(suggestions, 1):
        confidence_emoji = {'VERIFIED': '✅', 'LIKELY': '🟡', 'EXPERIMENTAL': '🔴'}
        emoji = confidence_emoji.get(s.confidence, '⚪')
        print(f"  ─── [{i}] {s.function_name}() ───────────────────────")
        print(f"  File:       {s.file_path}:{s.line_number}")
        print(f"  Guard:      {s.guard_type} on '{s.target_variable}'")
        print(f"  Confidence: {emoji} {s.confidence}")
        print()
        print(f"  Original:   {s.original_line.strip()}")
        print(f"  Fix:        {s.suggested_line.strip()}")
        print()
        print(f"  Why: {s.explanation}")
        print()
    print(f"  Run 'codesuture suggest --diff' for unified diffs.")
    print(f"  Run 'codesuture suggest <func_name>' for a specific function.")
    print()


def _handle_lifecycle(args):
    """Handle the 'lifecycle' command — manage patch lifecycle states."""
    from codesuture.lifecycle import LifecycleManager, PatchState

    mgr = LifecycleManager()
    action = getattr(args, 'lifecycle_action', None) or 'show'

    if action == 'summary':
        counts = mgr.summary()
        print()
        print("  CodeSuture Patch Lifecycle Summary")
        print()
        for state, count in counts.items():
            if state == 'total':
                continue
            emoji = {'patched': '🔧', 'persisted': '💾', 'suggested': '💡',
                     'verified': '✅', 'fixed': '🎉', 'expired': '⏰',
                     'rolled_back': '↩️', 'detected': '🔍', 'replayed': '🔄'}
            icon = emoji.get(state, '  ')
            print(f"  {icon} {state:<14} {count}")
        print(f"  ────────────────────")
        print(f"     Total:         {counts['total']}")
        print()
        return

    if action == 'stale':
        stale = mgr.get_stale()
        if not stale:
            print("[CodeSuture] No stale patches found.")
            return
        print(f"\n  Stale Patches ({len(stale)} patches persisted >5 days)\n")
        for p in stale:
            print(f"  ⚠️  {p.function_name}() — {p.guard_type} — {p.age_days()} days old")
        print()
        return

    if action == 'expired':
        expired = mgr.get_expired()
        if not expired:
            print("[CodeSuture] No expired patches.")
            return
        print(f"\n  Expired Patches ({len(expired)})\n")
        for p in expired:
            print(f"  ⏰ {p.function_name}() — TTL {p.ttl_days}d — {p.age_days()} days old")
        print()
        return

    if action == 'fix':
        func = args.func_name
        found = mgr.mark_fixed(func)
        if found:
            print(f"[CodeSuture] Marked patches for '{func}' as FIXED.")
        else:
            print(f"[CodeSuture] No patches found for '{func}'.")
        return

    # Default: show all
    all_patches = mgr.get_all()
    if not all_patches:
        print("[CodeSuture] No patch lifecycle data. Run a script to generate patches.")
        return
    print(f"\n  CodeSuture Patch Lifecycle ({len(all_patches)} patches)\n")
    for p in all_patches:
        state_emoji = {'patched': '🔧', 'persisted': '💾', 'suggested': '💡',
                       'verified': '✅', 'fixed': '🎉', 'expired': '⏰',
                       'rolled_back': '↩️', 'detected': '🔍', 'replayed': '🔄'}
        icon = state_emoji.get(p.current_state.value, '  ')
        transitions = len(p.transitions)
        print(f"  {icon} {p.function_name}() — {p.guard_type} — {p.current_state.value} ({transitions} transitions, {p.age_days()}d)")
    print()


def _handle_metrics(args):
    """Handle the 'metrics' command — export metrics."""
    from codesuture.metrics import MetricsCollector

    collector = MetricsCollector()
    fmt = getattr(args, 'metrics_format', 'prometheus')

    if fmt == 'json':
        print(collector.export_json())
    else:
        print(collector.export_prometheus())


if __name__ == '__main__':
    main()