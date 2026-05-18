import sys
import argparse
from codesuture.tracer import install, uninstall, _install_trace_on_all_threads

def main():
    parser = argparse.ArgumentParser(prog='codesuture',
                                     description='Runtime Python bytecode patcher with self-healing re-execution')
    parser.add_argument('--version', action='version', version='codesuture 0.7.0')
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
                             shadow=args.shadow, ttl=args.ttl)
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

if __name__ == '__main__':
    main()