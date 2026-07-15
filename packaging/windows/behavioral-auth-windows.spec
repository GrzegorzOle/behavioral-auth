# PyInstaller spec — one-folder Windows bundle for the behavioural-auth suite.
#
# The Windows counterpart of packaging/behavioral-auth.spec. Two differences from
# the Linux build:
#
#   * Windows symlinks need privilege, so the four command names are *real* .exe
#     files instead of symlinks to one binary. They all run packaging/launcher.py
#     and dispatch on argv[0] (the launcher strips the .exe suffix). MERGE keeps
#     the ~1 GB of shared libraries (torch CPU, onnx, cv2) in the bundle once.
#   * A fifth binary, behavioral-auth-service.exe, is the pywin32 service host
#     (packaging/windows/service.py) the installer registers with the SCM.
#
# Build on Windows with a venv that installed requirements.txt (pulls pynput +
# pywin32 via the win32 markers) and pyinstaller:
#
#     pyinstaller packaging/windows/behavioral-auth-windows.spec
#
# torch is the CPU wheel (requirements.txt), so no CUDA libraries are pulled in.
# Not runtime-verified on a real Windows box yet — see Planned work, Stage 2.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent.parent   # packaging/windows/ -> repo root

datas, binaries, hiddenimports = [], [], []
for pkg in ('torch', 'onnxruntime', 'onnx', 'cv2', 'duckdb', 'numpy', 'pandas',
            'pynput'):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules('behavioral_auth')

# pywin32 pieces the Event Log sink and the service use. PyInstaller ships hooks
# for these, but the service host also wants win32timezone at runtime, which
# nothing imports by name — pull them in explicitly so the frozen service starts.
hiddenimports += ['win32timezone', 'win32serviceutil', 'servicemanager',
                  'win32service', 'win32event', 'win32evtlog', 'win32evtlogutil',
                  'win32con', 'pywintypes']

# Runtime resources: the .sql migrations (importlib.resources) and the Windows
# default config (ProgramData paths + sink: eventlog), shipped as the bundled
# config so the suite runs out of the box; config_path() still lets a config next
# to the exe, or BEHAVIORAL_AUTH_CONFIG, override it.
datas += [
    (str(ROOT / 'src' / 'behavioral_auth' / 'db' / 'migrations'),
     'behavioral_auth/db/migrations'),
    (str(ROOT / 'packaging' / 'windows' / 'config.windows.yaml'), 'config'),
]

_EXCLUDES = ['triton', 'scipy', 'sklearn', 'tkinter', 'matplotlib',
             'pytest', 'IPython']

launcher_a = Analysis(
    [str(ROOT / 'packaging' / 'launcher.py')],
    pathex=[str(ROOT / 'src')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=_EXCLUDES,
    noarchive=False,
)

service_a = Analysis(
    [str(ROOT / 'packaging' / 'windows' / 'service.py')],
    pathex=[str(ROOT / 'src')],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    excludes=_EXCLUDES,
    noarchive=False,
)

# Share the heavy dependency set between the two analyses so _internal/ holds it
# once. MERGE must see every Analysis whose EXE goes into the same COLLECT.
MERGE((launcher_a, 'launcher', 'behavioral-authd'),
      (service_a, 'service', 'behavioral-auth-service'))

launcher_pyz = PYZ(launcher_a.pure)
service_pyz = PYZ(service_a.pure)

# The bundled default config carries an eventlog example inside config/config.yaml;
# the migrations ride along too. All datas live on launcher_a.

# Four command .exes, all running the launcher; argv[0] selects the command.
_CMDS = ['behavioral-authd', 'behavioral-auth', 'behavioral-report', 'behavioral-face']
exes = [
    EXE(launcher_pyz, launcher_a.scripts, [], exclude_binaries=True,
        name=name, console=True)
    for name in _CMDS
]

# The pywin32 service host. console=True so `... install/start/stop/remove` print.
service_exe = EXE(service_pyz, service_a.scripts, [], exclude_binaries=True,
                  name='behavioral-auth-service', console=True)

coll = COLLECT(
    *exes,
    launcher_a.binaries, launcher_a.datas,
    service_exe,
    service_a.binaries, service_a.datas,
    name='behavioral-auth',
)
