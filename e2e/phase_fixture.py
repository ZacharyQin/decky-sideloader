"""Bounded Unix checkpoints for observing durable core work before SIGKILL."""
import asyncio
import json


class BackupGate:
    def __init__(self, fixture, home, checkpoint='backup-first-chunk'):
        if checkpoint not in ('backup-first-chunk','install-verified'):
            raise ValueError('Unknown process checkpoint')
        self.checkpoint=checkpoint
        self.path=fixture/'backup-gate.sock'
        self.marker=home/'user/.sideloader-e2e-backup-gate'
        self.server=None
        self.clients=set()
        self.entered=False
        self.aborted=False
        self.released=False
        self.errors=[]
        self.release=asyncio.Event()

    async def start(self):
        self.server=await asyncio.start_unix_server(self.accept,path=self.path)
        self.marker_value=json.dumps({'socket':str(self.path),'checkpoint':self.checkpoint},sort_keys=True)+'\n'
        self.marker.write_text(self.marker_value)
        self.marker.chmod(0o600)
        return self

    async def accept(self,reader,writer):
        self.clients.add(asyncio.current_task())
        pending=[]
        try:
            line=await asyncio.wait_for(reader.readline(),3)
            if line!=(self.checkpoint+'\n').encode() or self.entered:
                raise ValueError('Unexpected or repeated process checkpoint')
            self.entered=True
            closed=asyncio.create_task(reader.read(1))
            released=asyncio.create_task(self.release.wait())
            pending=[closed,released]
            done,_=await asyncio.wait(pending,timeout=15,return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise TimeoutError('Backup checkpoint deadline')
            if closed in done:
                if await closed!=b'':
                    raise ValueError('Unexpected checkpoint payload')
                self.aborted=True
            else:
                writer.write(b'1')
                await writer.drain()
                self.released=True
        except Exception as error:
            self.errors.append(str(error))
        finally:
            for task in pending:
                if not task.done():task.cancel()
            if pending:await asyncio.gather(*pending,return_exceptions=True)
            writer.close()
            try:await writer.wait_closed()
            except ConnectionError:pass
            self.clients.discard(asyncio.current_task())

    def status(self):
        return {'checkpoint':self.checkpoint,'entered':self.entered,'aborted':self.aborted,'released':self.released,'errors':self.errors,'clients':len(self.clients),'listening':bool(self.server and self.server.is_serving())}

    def disable_for_next_core(self):
        if not self.entered or not self.aborted or self.clients:
            raise ValueError('Old checkpoint connection has not ended')
        if self.marker.is_symlink() or self.marker.read_text()!=self.marker_value:
            raise ValueError('Owned checkpoint marker changed')
        self.marker.unlink()
        return self.status()

    async def close(self):
        self.release.set()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.clients:await asyncio.wait_for(asyncio.gather(*tuple(self.clients)),3)
        if self.path.exists():self.path.unlink()
