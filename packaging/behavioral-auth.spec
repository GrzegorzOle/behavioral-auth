# PyInstaller spec — one-folder Linux bundle for the behavioural-auth suite.
#
# A single frozen binary (behavioral-authd) backs all four commands; build-linux.sh
# adds symlinks (behavioral-auth, behavioral-report, behavioral-face) next to it so
# argv[0] dispatches — see packaging/launcher.py. Build with:
#
#     pyinstaller packaging/behavioral-auth.spec
#
# torch is pinned to the CPU wheel (requirements.txt), so no CUDA libraries are
# pulled in here.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent   # packaging/ -> repo root

datas, binaries, hiddenimports = [], [], []
for pkg in ('torch', 'onnxruntime', 'onnx', 'cv2', 'duckdb', 'numpy', 'pandas'):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# The app's own submodules are imported lazily in places (the daemon builds its
# pipeline by name), so pull them all in explicitly.
hiddenimports += collect_submodules('behavioral_auth')

# Runtime resources: the .sql migrations (loaded via importlib.resources) and the
# default config (config_path() falls back to it inside a bundle).
datas += [
    (str(ROOT / 'src' / 'behavioral_auth' / 'db' / 'migrations'),
     'behavioral_auth/db/migrations'),
    (str(ROOT / 'config' / 'config.yaml'), 'config'),
]

a = Analysis(
    [str(ROOT / 'packaging' / 'launcher.py')],
    pathex=[str(ROOT / 'src')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # triton is torch's GPU-kernel compiler — dead weight in a CPU-only build
    # (~550 MB). scipy/sklearn are transitive and unused by this app. The rest
    # are dev/GUI packages that never run at runtime.
    excludes=['triton', 'scipy', 'sklearn', 'tkinter', 'matplotlib',
              'pytest', 'IPython'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='behavioral-authd',
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name='behavioral-auth',
)
