import asyncio
import logging
import signal
from weakref import WeakValueDictionary

import aio_pika
from pydantic import ValidationError

from .backend import Backend
from .config import Settings
from .errors import AuthenticationFailure, ChatDeleted, PermanentFailure
from .models import Task
from .session import ChatSession

logger = logging.getLogger(__name__)


async def process_delivery(message, session: ChatSession, settings: Settings, backend: Backend,
                           chat_locks: WeakValueDictionary | None = None):
    """Manual settlement: cancellation/unknown outcomes are never successful ACKs."""
    try:
        if len(message.body) > 4096:
            raise ValueError("Task too large")
        task = Task.model_validate_json(message.body)
    except (ValueError, ValidationError):
        logger.error("delivery_invalid_task")
        await message.reject(requeue=False)
        return
    count = max(
        int((message.headers or {}).get("x-delivery-count", 0)),
        int((message.headers or {}).get("x-acquired-count", 1)) - 1,
    )
    if count >= settings.max_delivery_attempts:
        logger.error("delivery_limit chat_id=%s attempts=%s", task.chat_id, count)
        await message.reject(requeue=False)
        return
    logger.info("delivery_started chat_id=%s attempt=%s", task.chat_id, count + 1)
    try:
        if chat_locks is None:
            await session.run(task.chat_id)
        else:
            lock = chat_locks.get(task.chat_id)
            if lock is None:
                lock = asyncio.Lock()
                chat_locks[task.chat_id] = lock
            if lock.locked():
                logger.info("delivery_waiting_same_chat chat_id=%s", task.chat_id)
            async with lock:
                await session.run(task.chat_id)
    except asyncio.CancelledError:
        logger.warning("delivery_interrupted chat_id=%s", task.chat_id)
        if not message.processed:
            await message.nack(requeue=True)
        raise
    except AuthenticationFailure:
        logger.error("delivery_authentication_failed chat_id=%s", task.chat_id)
        await message.nack(requeue=True)
        raise
    except ChatDeleted:
        logger.info("delivery_chat_deleted chat_id=%s", task.chat_id)
        # Terminal disposal, not a claim that the agent completed a deleted chat.
        await message.reject(requeue=False)
    except PermanentFailure as exc:
        logger.error("permanent_failure chat_id=%s reason=%s", task.chat_id, exc)
        # Session has closed SSE; never publish a status using a stale snapshot.
        await message.reject(requeue=False)
    except Exception as exc:
        # Log only exception class; URLs/tokens may appear in third-party exception strings.
        logger.warning("retry chat_id=%s reason=%s", task.chat_id, type(exc).__name__)
        await asyncio.sleep(settings.retry_backoff_seconds * min(8, 2**count))
        await message.nack(requeue=True)
    else:
        await message.ack()
        logger.info("delivery_acked chat_id=%s", task.chat_id)


async def consume_deliveries(deliveries, stop: asyncio.Event, session: ChatSession,
                             settings: Settings, backend: Backend):
    """Keep other chats moving while one delivery waits for user input on SSE."""
    active: set[asyncio.Task] = set()
    failures: list[BaseException] = []
    chat_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    def finished(task: asyncio.Task):
        active.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            failures.append(exc)
            stop.set()

    try:
        while not stop.is_set():
            incoming = asyncio.create_task(anext(deliveries))
            stopping = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    {incoming, stopping}, return_when=asyncio.FIRST_COMPLETED
                )
                if stopping in done:
                    break
                message = await incoming
                processing = asyncio.create_task(
                    process_delivery(message, session, settings, backend, chat_locks)
                )
                active.add(processing)
                processing.add_done_callback(finished)
            finally:
                for task in (incoming, stopping):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(incoming, stopping, return_exceptions=True)
    finally:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
    if failures:
        raise failures[0]


async def run_worker(settings: Settings, session: ChatSession, backend: Backend):
    settings.validate_runtime(worker=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # asyncio.Runner handles Ctrl+C on Windows.
    connection = await aio_pika.connect_robust(settings.rabbitmq_url.get_secret_value())
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=settings.agent_max_concurrent_chats)
        # Broker topology and DLQ belong to the shared infrastructure owner.
        queue = await channel.declare_queue(settings.agent_queue, passive=True)
        logger.info("worker_ready queue=%s max_concurrent_chats=%s", queue.name,
                    settings.agent_max_concurrent_chats)
        async with queue.iterator() as deliveries:
            await consume_deliveries(deliveries, stop, session, settings, backend)
