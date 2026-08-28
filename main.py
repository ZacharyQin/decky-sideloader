import asyncio
import decky


class Plugin:
    async def ping(self) -> str:
        return "pong"

    async def emit_demo_event(self):
        await asyncio.sleep(1)
        await decky.emit("demo_event", "Hello from Python")

    async def _main(self):
        decky.logger.info("GitHub Sideload starting")

    async def _unload(self):
        decky.logger.info("GitHub Sideload unloading")

    async def _uninstall(self):
        decky.logger.info("GitHub Sideload uninstall cleanup")
