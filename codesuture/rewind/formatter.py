"""
codesuture.rewind.formatter
Human-readable timeline formatting for crash forensics output.

Renders a list of :class:`~codesuture.rewind.buffer.FrameSnapshot` objects
into a readable crash-timeline suitable for CLI display.
"""

from typing import List, Optional

from codesuture.rewind.buffer import FrameSnapshot


def format_rewind_timeline(
    snapshots: List[FrameSnapshot],
    crash_time: Optional[float] = None,
) -> str:
    """Format a list of snapshots into a human-readable timeline.

    Parameters
    ----------
    snapshots:
        Ordered list of frame snapshots to render.
    crash_time:
        The ``time.monotonic()`` value of the crash event.  If *None*,
        the timestamp of the last exception snapshot (or the final
        snapshot) is used as the reference point.

    Returns
    -------
    str
        Multi-line string ready for ``print()`` or logging.
    """
    if not snapshots:
        return '[CodeSuture Rewind] No recorded events.\n'

    if crash_time is None:
        # Find the last exception event
        for s in reversed(snapshots):
            if s.event == 'exception':
                crash_time = s.timestamp
                break
        if crash_time is None:
            crash_time = snapshots[-1].timestamp

    lines: list[str] = []
    lines.append('[CodeSuture Rewind] Crash timeline')
    lines.append('=' * 60)
    lines.append('')

    for snap in snapshots:
        delta = snap.timestamp - crash_time
        sign = '+' if delta >= 0 else ''
        time_str = f'{sign}{delta:.3f}s'

        if snap.event == 'call':
            args_str = ', '.join(f'{k}={v}' for k, v in snap.args.items())
            lines.append(
                f'  {time_str:>10}  CALL   '
                f'{snap.module}.{snap.function}({args_str})'
            )
        elif snap.event == 'return':
            lines.append(
                f'  {time_str:>10}  RETURN '
                f'{snap.module}.{snap.function} → {snap.return_value}'
            )
        elif snap.event == 'exception':
            lines.append(
                f'  {time_str:>10}  💥 EXCEPTION in {snap.module}.{snap.function}: {snap.exception}'
            )
            if snap.locals_snapshot:
                locals_str = ', '.join(
                    f'{k}={v}'
                    for k, v in list(snap.locals_snapshot.items())[:5]
                )
                lines.append(f'  {"":>10}         locals: {locals_str}')

    lines.append('')
    return '\n'.join(lines)
