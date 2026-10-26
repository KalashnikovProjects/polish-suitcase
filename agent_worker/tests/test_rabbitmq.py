"""Opt-in real broker check; unique queues only in a disposable test vhost."""

import asyncio
import os
from uuid import uuid4

import aio_pika
import pytest

from ostrovok_agent.worker import process_delivery


@pytest.mark.rabbitmq
async def test_real_manual_ack_nack_and_dead_letter(settings):
    url = os.environ.get("RABBITMQ_TEST_URL")
    if not url:
        pytest.skip("Set RABBITMQ_TEST_URL to a disposable RabbitMQ vhost")
    connection = await aio_pika.connect(url)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        name = "ostrovok-agent-test-" + uuid4().hex
        dlq = await channel.declare_queue(name + ".dlq", durable=True)
        queue = await channel.declare_queue(
            name,
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                "x-quorum-initial-group-size": 1,
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": dlq.name,
            },
        )
        try:

            async def publish(body=b'{"chat_id":1}'):
                await channel.default_exchange.publish(
                    aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                    routing_key=queue.name,
                )

            class Session:
                def __init__(self, fail=False):
                    self.fail = fail

                async def run(self, chat_id):
                    if self.fail:
                        raise ConnectionError("simulated SSE loss")

            await publish()
            delivery = await queue.get(timeout=5)
            await process_delivery(delivery, Session(), settings, None)
            assert delivery.processed
            assert await queue.get(fail=False) is None

            await publish()
            delivery = await queue.get(timeout=5)
            await process_delivery(delivery, Session(fail=True), settings, None)
            retried = await queue.get(timeout=5)
            assert retried.redelivered
            await process_delivery(retried, Session(), settings, None)

            await publish(b'{"chat_id":true}')
            await process_delivery(await queue.get(timeout=5), Session(), settings, None)
            async with dlq.iterator() as dead_letters, asyncio.timeout(5):
                dead = await anext(dead_letters)
                await dead.ack()
                assert dead.body == b'{"chat_id":true}'
        finally:
            await queue.delete(if_unused=False, if_empty=False)
            await dlq.delete(if_unused=False, if_empty=False)
