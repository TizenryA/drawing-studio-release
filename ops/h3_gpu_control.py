"""Run a bounded operational cell against the existing Draw GPU."""
import asyncio,json,sys
from backend.runtime import Runtime
from backend.database import Database
from pathlib import Path
DB=Database(Path('/data/studio.sqlite3'))
async def run():
    rt=Runtime(lambda: DB.get('settings','main'),lambda *args:None)
    code=sys.stdin.read()
    result=await rt.execute(code,timeout=55,interrupt_on_timeout=False)
    print(json.dumps(result,ensure_ascii=False))
asyncio.run(run())
