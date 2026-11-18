"""uvicorn loop factory: `uvicorn app.main:app --loop app.loop:selector_loop_factory`.

psycopg (LangGraph's Postgres checkpointer) can't run on Windows' default ProactorEventLoop;
the selector loop works everywhere. On Linux (deploy) this is the same as uvicorn's default.
uvicorn calls a custom `--loop` import string directly as the loop factory.
"""

import asyncio
import selectors


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())
