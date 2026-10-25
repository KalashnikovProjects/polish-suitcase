import asyncio
import logging
import signal

import aio_pika
from pydantic import ValidationError

from .backend import Backend
from .config import Settings
from .errors import AuthenticationFailure, ChatDeleted, PermanentFailure
from .models import Task
from .session import ChatSession

logger = logging.getLogger(__name__)


async def process_delivery(message, session: ChatSession, settings: Settings, backend: Backend):
    """Manual settlement: cancellation/unknown outcomes are never successful ACKs."""
    try:
        if len(message.body) > 4096:
            raise ValueError("Task too large")
        task = Task.model_validate_json(message.body)
    except (ValueError, ValidationError):
        await message.reject(requeue=False)
        return
    count = max(
        int((message.headers or {}).get("x-delivery-count", 0)),
        int((message.headers or {}).get("x-acquired-count", 1)) - 1,
    )
    if count >= settings.max_delivery_attempts:
        logger.error("delivery_limit chat_id=%s", task.chat_id)
        await message.reject(requeue=False)
        return
    try:
        await session.run(task.chat_id)
    except asyncio.CancelledError:
        if not message.processed:
            await message.nack(requeue=True)
        raise
    except AuthenticationFailure:
        await message.nack(requeue=True)
        raise
    except ChatDeleted:
        # Terminal disposal, not a claim that the agent completed a deleted chat.
        await message.reject(requeue=False)
    except PermanentFailure:
        logger.error("permanent_failure chat_id=%s", task.chat_id)
        # Session has closed SSE; never publish a status using a stale snapshot.
        await message.reject(requeue=False)
    except Exception as exc:
        # Log only exception class; URLs/tokens may appear in third-party exception strings.
        logger.warning("retry chat_id=%s reason=%s", task.chat_id, type(exc).__name__)
        await asyncio.sleep(settings.retry_backoff_seconds * min(8, 2**count))
        await message.nack(requeue=True)
    else:
        await message.ack()


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
        await channel.set_qos(prefetch_count=1)
        # Broker topology and DLQ belong to the shared infrastructure owner.
        queue = await channel.declare_queue(settings.agent_queue, passive=True)
        async with queue.iterator() as deliveries:
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
                        process_delivery(message, session, settings, backend)
                    )
                    try:
                        done, _ = await asyncio.wait(
                            {processing, stopping}, return_when=asyncio.FIRST_COMPLETED
                        )
                        if stopping in done:
                            processing.cancel()
                        try:
                            await processing
                        except asyncio.CancelledError:
                            if not stop.is_set():
                                raise
                    finally:
                        if not processing.done():
                            processing.cancel()
                        await asyncio.gather(processing, return_exceptions=True)
                finally:
                    for task in (incoming, stopping):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(incoming, stopping, return_exceptions=True)
