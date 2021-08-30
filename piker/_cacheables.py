# piker: trading gear for hackers
# Copyright (C) Tyler Goodlet (in stewardship for piker0)

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Cacheing apis and toolz.

"""
# further examples of interest:
# https://gist.github.com/njsmith/cf6fc0a97f53865f2c671659c88c1798#file-cache-py-L8

from collections import OrderedDict
from typing import (
    Any,
    Hashable,
    Optional,
    TypeVar,
    AsyncContextManager,
)
from contextlib import (
    asynccontextmanager,
    AsyncExitStack,
)

import trio
from trio_typing import TaskStatus
from tractor._portal import maybe_open_nursery

from .brokers import get_brokermod
from .log import get_logger


T = TypeVar('T')
log = get_logger(__name__)


def async_lifo_cache(maxsize=128):
    """Async ``cache`` with a LIFO policy.

    Implemented my own since no one else seems to have
    a standard. I'll wait for the smarter people to come
    up with one, but until then...
    """
    cache = OrderedDict()

    def decorator(fn):

        async def wrapper(*args):
            key = args
            try:
                return cache[key]
            except KeyError:
                if len(cache) >= maxsize:
                    # discard last added new entry
                    cache.popitem()

                # do it
                cache[key] = await fn(*args)
                return cache[key]

        return wrapper

    return decorator


_cache: dict[str, 'Client'] = {}  # noqa


# XXX: this mis mostly an alt-implementation of
# maybe_open_ctx() below except it uses an async exit statck.
# ideally wer pick one or the other.
@asynccontextmanager
async def open_cached_client(
    brokername: str,
) -> 'Client':  # noqa
    '''Get a cached broker client from the current actor's local vars.

    If one has not been setup do it and cache it.
    '''
    global _cache

    clients = _cache.setdefault('clients', {'_lock': trio.Lock()})

    # global cache task lock
    lock = clients['_lock']

    client = None

    try:
        log.info(f"Loading existing `{brokername}` client")

        async with lock:
            client = clients[brokername]
            client._consumers += 1

        yield client

    except KeyError:
        log.info(f"Creating new client for broker {brokername}")

        async with lock:
            brokermod = get_brokermod(brokername)
            exit_stack = AsyncExitStack()

            client = await exit_stack.enter_async_context(
                brokermod.get_client()
            )
            client._consumers = 0
            client._exit_stack = exit_stack
            clients[brokername] = client

        yield client

    finally:
        if client is not None:
            # if no more consumers, teardown the client
            client._consumers -= 1
            if client._consumers <= 0:
                await client._exit_stack.aclose()


class cache:
    '''Globally (processs wide) cached, task access to a
    kept-alive-while-in-use async resource.

    '''
    lock = trio.Lock()
    users: int = 0
    values: dict[tuple[str, str], tuple[AsyncExitStack, Any]] = {}
    nurseries: dict[int, Optional[trio.Nursery]] = {}
    no_more_users: Optional[trio.Event] = None

    @classmethod
    async def run_ctx(
        cls,
        mng,
        key,
        task_status: TaskStatus[T] = trio.TASK_STATUS_IGNORED,

    ) -> None:
        async with mng as value:

            cls.no_more_users = trio.Event()
            cls.values[key] = value
            task_status.started(value)
            try:
                await cls.no_more_users.wait()
            finally:
                value = cls.values.pop(key)
                # discard nursery ref so it won't be re-used (an error)
                cls.nurseries.pop(id(mng))


@asynccontextmanager
async def maybe_open_ctx(

    key: Hashable,
    mngr: AsyncContextManager[T],

) -> (bool, T):
    '''Maybe open a context manager if there is not already a cached
    version for the provided ``key``. Return the cached instance on
    a cache hit.

    '''

    await cache.lock.acquire()

    ctx_key = id(mngr)

    # TODO: does this need to be a tractor "root nursery"?
    async with maybe_open_nursery(cache.nurseries.get(ctx_key)) as n:
        cache.nurseries[ctx_key] = n

        value = None
        try:
            # lock feed acquisition around task racing  / ``trio``'s
            # scheduler protocol
            value = cache.values[key]
            log.info(f'Reusing cached feed for {key}')
            cache.users += 1
            cache.lock.release()
            yield True, value

        except KeyError:
            log.info(f'Allocating new feed for {key}')

            # **critical section** that should prevent other tasks from
            # checking the cache until complete otherwise the scheduler
            # may switch and by accident we create more then one feed.

            value = await n.start(cache.run_ctx, mngr, key)
            cache.users += 1
            cache.lock.release()

            yield False, value

        finally:
            cache.users -= 1

            if cache.lock.locked():
                cache.lock.release()

            if value is not None:
                # if no more consumers, teardown the client
                if cache.users <= 0:
                    log.warning(f'De-allocating feed for {key}')

                    # terminate mngr nursery
                    cache.no_more_users.set()
