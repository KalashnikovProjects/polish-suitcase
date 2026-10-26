import json

from app import publisher as publisher_module


def test_successful_pika_publication_marks_matching_generation(monkeypatch):
    published = []
    marked = []

    class Channel:
        def exchange_declare(self, **_kwargs):
            pass

        def queue_declare(self, **_kwargs):
            pass

        def queue_bind(self, **_kwargs):
            pass

        def confirm_delivery(self):
            pass

        def basic_publish(self, **kwargs):
            published.append(kwargs)
            return None  # Pika BlockingChannel returns None after a successful confirm.

    class Connection:
        is_open = True

        def channel(self):
            return Channel()

        def close(self):
            self.is_open = False

    monkeypatch.setattr(publisher_module.pika, "BlockingConnection", lambda _params: Connection())

    class Store:
        def pending_jobs(self):
            return [(7, 3)]

        def mark_published(self, chat_id, generation):
            marked.append((chat_id, generation))
            publisher.stop_event.set()

    publisher = publisher_module.Publisher(Store())
    publisher._run()
    assert marked == [(7, 3)]
    assert json.loads(published[0]["body"]) == {"chat_id": 7}
    assert published[0]["mandatory"] is True


def test_nacked_publication_stays_pending_and_does_not_block_other_chats(monkeypatch):
    marked = []

    class Channel:
        def exchange_declare(self, **_kwargs):
            pass

        def queue_declare(self, **_kwargs):
            pass

        def queue_bind(self, **_kwargs):
            pass

        def confirm_delivery(self):
            pass

        def basic_publish(self, **kwargs):
            if json.loads(kwargs["body"])["chat_id"] == 7:
                raise publisher_module.pika.exceptions.NackError([])

    class Connection:
        is_open = True

        def channel(self):
            return Channel()

        def close(self):
            self.is_open = False

    monkeypatch.setattr(publisher_module.pika, "BlockingConnection", lambda _params: Connection())

    class Store:
        def pending_jobs(self):
            return [(7, 3), (8, 4)]

        def mark_published(self, chat_id, generation):
            marked.append((chat_id, generation))
            publisher.stop_event.set()

    publisher = publisher_module.Publisher(Store())
    publisher._run()
    assert marked == [(8, 4)]
