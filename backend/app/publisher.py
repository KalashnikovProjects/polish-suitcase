"""Recoverable queue publication from the chat document's pending marker."""

import json
import logging
import os
import threading
from urllib.parse import quote

import pika

from .storage import ChatStore


LOG = logging.getLogger(__name__)
QUEUE = "agent.tasks"
DLQ = "agent.tasks.dlq"
DLX = "agent.dlx"


def rabbit_url() -> str:
    user = quote(os.getenv("RABBITMQ_USER", "travel"), safe="")
    password = quote(os.getenv("RABBITMQ_PASSWORD", "travel_dev_password"), safe="")
    host = os.getenv("RABBITMQ_HOST", "rabbitmq")
    port = os.getenv("RABBITMQ_PORT", "5672")
    return f"amqp://{user}:{password}@{host}:{port}/%2F"


def declare_queues(channel) -> None:
    channel.exchange_declare(exchange=DLX, exchange_type="direct", durable=True)
    channel.queue_declare(queue=DLQ, durable=True, arguments={"x-queue-type": "quorum"})
    channel.queue_bind(queue=DLQ, exchange=DLX, routing_key=QUEUE)
    channel.queue_declare(queue=QUEUE, durable=True, arguments={
        "x-queue-type": "quorum",
        "x-delivery-limit": 5,
        "x-dead-letter-exchange": DLX,
    })


class Publisher:
    def __init__(self, store: ChatStore):
        self.store = store
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="rabbit-publisher", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def wake(self) -> None:
        self.wake_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join(timeout=10)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            connection = None
            try:
                connection = pika.BlockingConnection(pika.URLParameters(rabbit_url()))
                channel = connection.channel()
                declare_queues(channel)
                channel.confirm_delivery()
                while not self.stop_event.is_set():
                    for chat_id, job_generation in self.store.pending_jobs():
                        try:
                            channel.basic_publish(
                                exchange="", routing_key=QUEUE,
                                body=json.dumps({"chat_id": chat_id}).encode(),
                                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                                mandatory=True,
                            )
                        except (pika.exceptions.NackError, pika.exceptions.UnroutableError):
                            LOG.exception("RabbitMQ rejected task for chat %s; retrying later", chat_id)
                            continue
                        self.store.mark_published(chat_id, job_generation)
                    if self.stop_event.is_set():
                        break
                    self.wake_event.wait(timeout=2)
                    self.wake_event.clear()
                    connection.process_data_events(time_limit=0)
            except Exception:
                LOG.exception("RabbitMQ publisher retrying")
                self.stop_event.wait(2)
            finally:
                if connection and connection.is_open:
                    connection.close()
