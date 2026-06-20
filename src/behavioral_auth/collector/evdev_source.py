import asyncio, os, signal, socket, sys, uuid
from datetime import datetime, timezone
import duckdb, evdev
from loguru import logger
from behavioral_auth.config import load_settings
from behavioral_auth.collector.device_detector import detect_devices, is_keyboard_device
from behavioral_auth.collector.writer import Writer

def infer_dev_type(dev):
    return 'keyboard' if is_keyboard_device(dev) else 'mouse'

async def read_loop(path, writer, session_id):
    dev = evdev.InputDevice(path)
    dev_type = infer_dev_type(dev)
    logger.info(f'{path} {dev.name} -> {dev_type}')
    async for ev in dev.async_read_loop():
        if ev.type not in (evdev.ecodes.EV_KEY, evdev.ecodes.EV_REL, evdev.ecodes.EV_ABS, evdev.ecodes.EV_MSC):
            continue
        ts_ns = ev.sec * 1_000_000_000 + ev.usec * 1_000
        ts_utc = datetime.fromtimestamp(ev.sec + ev.usec / 1_000_000, tz=timezone.utc)
        writer.add((ts_ns, ts_utc, session_id, path, dev.name, dev_type, ev.type, ev.code, ev.value))

async def run_collector():
    cfg = load_settings()
    logger.remove(); logger.add(sys.stderr, level=cfg.general.log_level)
    # Allow verify_cmd to inject a session_id so all events go to ONE session
    session_id = os.getenv('BEHAVIORAL_SESSION_ID') or str(uuid.uuid4())
    conn = duckdb.connect(cfg.storage.db_path)
    # Only insert session row if it doesn't exist yet
    existing = conn.execute('SELECT 1 FROM sessions WHERE session_id = ?', [session_id]).fetchone()
    if not existing:
        conn.execute('INSERT INTO sessions (session_id, user_name, host_name, mode, metadata) VALUES (?, ?, ?, ?, ?)',
                     [session_id, os.getenv('USER', 'unknown'), socket.gethostname(), cfg.general.mode, '{}'])
    conn.close()
    writer = Writer()
    devices = detect_devices(cfg.collector.devices)
    if not devices:
        logger.error('No keyboard/mouse devices found')
        return
    tasks = [asyncio.create_task(read_loop(d, writer, session_id)) for d in devices]
    loop = asyncio.get_running_loop()
    async def shutdown():
        for t in tasks: t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close(); os._exit(0)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))
    await asyncio.gather(*tasks, return_exceptions=True)
