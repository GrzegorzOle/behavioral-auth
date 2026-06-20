import asyncio
from behavioral_auth.collector.evdev_source import run_collector

def main():
    asyncio.run(run_collector())
