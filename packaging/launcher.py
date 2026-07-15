"""Multi-call entry point for the bundled behavioural-auth suite.

One frozen binary backs all four commands. In the bundle the three extra
command names are symlinks to this executable, so ``argv[0]`` selects which one
runs — the busybox trick. That keeps the ~700 MB of shared libraries (torch,
opencv, onnxruntime) in the bundle exactly once instead of four times.

It also accepts the command as the first argument (``<binary> authd ...``), so a
bare AppImage whose ``argv[0]`` is the image name still reaches every command.

On Windows the four commands are real ``.exe`` files (symlinks need privilege
there), so the executable's own ``.exe`` suffix is stripped before the argv[0]
lookup — ``behavioral-authd.exe`` has to resolve to ``behavioral-authd``.
"""

import importlib
import os
import sys

# command name -> (module, callable)
_COMMANDS = {
    'behavioral-authd': ('behavioral_auth.daemon.main', 'main'),
    'behavioral-auth': ('behavioral_auth.cli.main', 'main'),
    'behavioral-report': ('behavioral_auth.cli.report_cmd', 'main'),
    'behavioral-face': ('behavioral_auth.cli.face_cmd', 'main'),
}


def _run(name: str) -> None:
    module, func = _COMMANDS[name]
    getattr(importlib.import_module(module), func)()


def _force_utf8_stdio() -> None:
    """On Windows the console/pipe encoding defaults to a legacy codepage
    (cp1252), so any non-ASCII output — the CLI's Polish status text, the ●/○
    glyphs, alarm messages — raises UnicodeEncodeError and kills the command.
    Force UTF-8 with a replacing fallback so output degrades to '?' rather than
    crashing. Windows-only: Linux already uses UTF-8 and this leaves it alone."""
    if sys.platform != 'win32':
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _force_utf8_stdio()
    name = os.path.splitext(os.path.basename(sys.argv[0]))[0]   # drop .exe on Windows
    if name in _COMMANDS:
        _run(name)
        return

    # Not invoked through a command-named symlink: take the command as argv[1].
    short = {n.removeprefix('behavioral-'): n for n in _COMMANDS}   # authd, auth, ...
    if len(sys.argv) > 1 and (sys.argv[1] in _COMMANDS or sys.argv[1] in short):
        chosen = short.get(sys.argv[1], sys.argv[1])
        sys.argv = [chosen, *sys.argv[2:]]                          # argparse sees the real prog
        _run(chosen)
        return

    sys.stderr.write(
        'behavioral-auth suite — invoke as one of:\n  '
        + '\n  '.join(sorted(_COMMANDS))
        + '\nor: <binary> <command> [args...]  (command may drop the behavioral- prefix)\n'
    )
    sys.exit(2)


if __name__ == '__main__':
    main()
