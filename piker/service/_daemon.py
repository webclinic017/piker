# piker: trading gear for hackers
# Copyright (C) Tyler Goodlet (in stewardship for pikers)

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
Daemon-actor spawning "endpoint-hooks".

"""
from __future__ import annotations
from typing import (
    Optional,
    Callable,
    Any,
)
from contextlib import (
    asynccontextmanager as acm,
)

import tractor

from ..log import (
    get_logger,
    get_console_log,
)
from ..brokers import get_brokermod
from ._mngr import (
    Services,
)
from ._registry import find_service

log = get_logger(__name__)

# `brokerd` enabled modules
# NOTE: keeping this list as small as possible is part of our caps-sec
# model and should be treated with utmost care!
_data_mods = [
    'piker.brokers.core',
    'piker.brokers.data',
    'piker.data',
    'piker.data.feed',
    'piker.data._sampling'
]


@acm
async def maybe_spawn_daemon(

    service_name: str,
    service_task_target: Callable,
    spawn_args: dict[str, Any],
    loglevel: Optional[str] = None,

    singleton: bool = False,
    **kwargs,

) -> tractor.Portal:
    '''
    If no ``service_name`` daemon-actor can be found,
    spawn one in a local subactor and return a portal to it.

    If this function is called from a non-pikerd actor, the
    spawned service will persist as long as pikerd does or
    it is requested to be cancelled.

    This can be seen as a service starting api for remote-actor
    clients.

    '''
    if loglevel:
        get_console_log(loglevel)

    # serialize access to this section to avoid
    # 2 or more tasks racing to create a daemon
    lock = Services.locks[service_name]
    await lock.acquire()

    async with find_service(service_name) as portal:
        if portal is not None:
            lock.release()
            yield portal
            return

    log.warning(f"Couldn't find any existing {service_name}")

    # TODO: really shouldn't the actor spawning be part of the service
    # starting method `Services.start_service()` ?

    # ask root ``pikerd`` daemon to spawn the daemon we need if
    # pikerd is not live we now become the root of the
    # process tree
    from . import maybe_open_pikerd
    async with maybe_open_pikerd(

        loglevel=loglevel,
        **kwargs,

    ) as pikerd_portal:

        # we are the root and thus are `pikerd`
        # so spawn the target service directly by calling
        # the provided target routine.
        # XXX: this assumes that the target is well formed and will
        # do the right things to setup both a sub-actor **and** call
        # the ``_Services`` api from above to start the top level
        # service task for that actor.
        started: bool
        if pikerd_portal is None:
            started = await service_task_target(**spawn_args)

        else:
            # tell the remote `pikerd` to start the target,
            # the target can't return a non-serializable value
            # since it is expected that service startingn is
            # non-blocking and the target task will persist running
            # on `pikerd` after the client requesting it's start
            # disconnects.
            started = await pikerd_portal.run(
                service_task_target,
                **spawn_args,
            )

        if started:
            log.info(f'Service {service_name} started!')

        async with tractor.wait_for_actor(service_name) as portal:
            lock.release()
            yield portal
            await portal.cancel_actor()


async def spawn_brokerd(

    brokername: str,
    loglevel: Optional[str] = None,
    **tractor_kwargs,

) -> bool:

    log.info(f'Spawning {brokername} broker daemon')

    brokermod = get_brokermod(brokername)
    dname = f'brokerd.{brokername}'

    extra_tractor_kwargs = getattr(brokermod, '_spawn_kwargs', {})
    tractor_kwargs.update(extra_tractor_kwargs)

    # ask `pikerd` to spawn a new sub-actor and manage it under its
    # actor nursery
    modpath = brokermod.__name__
    broker_enable = [modpath]
    for submodname in getattr(
        brokermod,
        '__enable_modules__',
        [],
    ):
        subpath = f'{modpath}.{submodname}'
        broker_enable.append(subpath)

    portal = await Services.actor_n.start_actor(
        dname,
        enable_modules=_data_mods + broker_enable,
        loglevel=loglevel,
        debug_mode=Services.debug_mode,
        **tractor_kwargs
    )

    # non-blocking setup of brokerd service nursery
    from ..data import _setup_persistent_brokerd

    await Services.start_service_task(
        dname,
        portal,
        _setup_persistent_brokerd,
        brokername=brokername,
    )
    return True


@acm
async def maybe_spawn_brokerd(

    brokername: str,
    loglevel: Optional[str] = None,
    **kwargs,

) -> tractor.Portal:
    '''
    Helper to spawn a brokerd service *from* a client
    who wishes to use the sub-actor-daemon.

    '''
    async with maybe_spawn_daemon(

        f'brokerd.{brokername}',
        service_task_target=spawn_brokerd,
        spawn_args={
            'brokername': brokername,
            'loglevel': loglevel,
        },
        loglevel=loglevel,
        **kwargs,

    ) as portal:
        yield portal


async def spawn_emsd(

    loglevel: Optional[str] = None,
    **extra_tractor_kwargs

) -> bool:
    """
    Start the clearing engine under ``pikerd``.

    """
    log.info('Spawning emsd')

    portal = await Services.actor_n.start_actor(
        'emsd',
        enable_modules=[
            'piker.clearing._ems',
            'piker.clearing._client',
        ],
        loglevel=loglevel,
        debug_mode=Services.debug_mode,  # set by pikerd flag
        **extra_tractor_kwargs
    )

    # non-blocking setup of clearing service
    from ..clearing._ems import _setup_persistent_emsd

    await Services.start_service_task(
        'emsd',
        portal,
        _setup_persistent_emsd,
    )
    return True


@acm
async def maybe_open_emsd(

    brokername: str,
    loglevel: Optional[str] = None,
    **kwargs,

) -> tractor._portal.Portal:  # noqa

    async with maybe_spawn_daemon(

        'emsd',
        service_task_target=spawn_emsd,
        spawn_args={'loglevel': loglevel},
        loglevel=loglevel,
        **kwargs,

    ) as portal:
        yield portal
