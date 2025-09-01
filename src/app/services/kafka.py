# src/app/services/kafka.py
from __future__ import annotations
import os, asyncio
from typing import Optional
from aiokafka import AIOKafkaProducer

_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
_producer: Optional[AIOKafkaProducer] = None
_lock = asyncio.Lock()

async def kafka_producer() -> AIOKafkaProducer:
    global _producer
    if _producer and _producer._closed is False:
        return _producer
    async with _lock:
        if _producer and _producer._closed is False:
            return _producer
        _producer = AIOKafkaProducer(bootstrap_servers=_BOOTSTRAP, linger_ms=5)
        await _producer.start()
        return _producer

async def kafka_send(topic: str, key: bytes, value: bytes) -> None:
    prod = await kafka_producer()
    await prod.send_and_wait(topic, value=value, key=key)

async def kafka_close():
    global _producer
    if _producer:
        await _producer.stop()
        _producer = None
