"""Bounded, best-effort RGB IPC. No camera, Qt, or serial dependencies.

The launcher owns the shared memory. Spawned children share its resource tracker.
Two independently locked slots prevent torn reads; every lock attempt is nonblocking.
A crashed reader can strand one slot without stopping the producer in the other.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from multiprocessing import shared_memory
import struct
import time
from typing import Protocol

import numpy as np

_HEADER = struct.Struct('<QdII')  # sequence, UNIX timestamp, height, width
_STATUS = struct.Struct("<64si3d")  # stage, inspection index, observed R/P/Z (NaN if unknown)
DEFAULT_CAPACITY = 1920 * 1080 * 3


class PreviewSink(Protocol):
    def publish_rgb(self, frame: np.ndarray, metadata=None) -> None: ...


def publish_safely(sink, bgr: np.ndarray) -> None:
    if sink is None:
        return
    try:
        # Production helpers use BGR. Only the observer's copy is converted.
        sink.publish_rgb(bgr[..., ::-1])
    except Exception:
        if not getattr(publish_safely, '_warned', False):
            logging.warning('[UI WARNING] preview unavailable')
            publish_safely._warned = True


@dataclass
class PreviewChannel:
    name: str
    capacity: int
    locks: tuple
    status_lock: object

    @classmethod
    def create(cls, context, capacity=DEFAULT_CAPACITY):
        memory = shared_memory.SharedMemory(create=True, size=2 * (_HEADER.size + capacity) + _STATUS.size)
        try:
            memory.buf[:] = b'\0' * memory.size
            channel = cls(memory.name, capacity, (context.Lock(), context.Lock()), context.Lock())
            _STATUS.pack_into(memory.buf, 2 * (_HEADER.size + capacity), b"", -1,
                              float("nan"), float("nan"), float("nan"))
        except BaseException:
            memory.unlink()
            raise
        finally:
            memory.close()
        return channel

    def unlink(self):
        try:
            memory = shared_memory.SharedMemory(name=self.name)
        except FileNotFoundError:
            return
        try:
            memory.unlink()
        finally:
            memory.close()


class SharedMemoryPreviewPublisher:
    def __init__(self, channel: PreviewChannel, fps=10):
        self.channel = channel
        self.memory = shared_memory.SharedMemory(name=channel.name)
        self.interval = 1.0 / fps
        self.last_publish = float('-inf')
        self.sequence = 0

    def publish_rgb(self, frame, metadata=None):
        now = time.monotonic()
        if now - self.last_publish < self.interval:
            return
        if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError('preview requires uint8 HxWx3 RGB')
        if frame.nbytes > self.channel.capacity:
            raise ValueError('preview frame exceeds channel capacity')
        for index in (self.sequence % 2, (self.sequence + 1) % 2):
            lock = self.channel.locks[index]
            if not lock.acquire(False):
                continue
            try:
                offset = index * (_HEADER.size + self.channel.capacity)
                target = np.ndarray(frame.shape, dtype=np.uint8, buffer=self.memory.buf,
                                    offset=offset + _HEADER.size)
                np.copyto(target, frame)
                del target
                self.sequence += 1
                _HEADER.pack_into(self.memory.buf, offset, self.sequence, time.time(),
                                  frame.shape[0], frame.shape[1])
                self.last_publish = now
            finally:
                lock.release()
            return

    def update_metadata(self, *, stage, pose_index=-1, roll=None, pitch=None, z=None):
        if not self.channel.status_lock.acquire(False):
            return
        try:
            values = [float('nan') if v is None else float(v) for v in (roll, pitch, z)]
            _STATUS.pack_into(self.memory.buf, 2 * (_HEADER.size + self.channel.capacity),
                              stage.encode('utf-8')[:63], pose_index, *values)
        finally:
            self.channel.status_lock.release()

    def close(self):
        self.memory.close()


class PreviewReader:
    def __init__(self, channel: PreviewChannel):
        self.channel = channel
        self.memory = shared_memory.SharedMemory(name=channel.name)
        self.sequence = 0

    def read_latest(self):
        newest = None
        for index, lock in enumerate(self.channel.locks):
            if not lock.acquire(False):
                continue
            try:
                offset = index * (_HEADER.size + self.channel.capacity)
                seq, timestamp, height, width = _HEADER.unpack_from(self.memory.buf, offset)
                if seq <= self.sequence or (newest is not None and seq <= newest[1]['sequence']):
                    continue
                if height == 0 or width == 0 or height * width * 3 > self.channel.capacity:
                    continue
                frame = np.ndarray((height, width, 3), dtype=np.uint8,
                                   buffer=self.memory.buf, offset=offset + _HEADER.size).copy()
                newest = (frame, {'sequence': seq, 'timestamp': timestamp,
                                  'shape': (height, width, 3)})
            finally:
                lock.release()
        if newest is not None:
            self.sequence = newest[1]['sequence']
        return newest

    def read_metadata(self):
        if not self.channel.status_lock.acquire(False):
            return None
        try:
            stage, index, roll, pitch, z = _STATUS.unpack_from(
                self.memory.buf, 2 * (_HEADER.size + self.channel.capacity))
            return dict(stage=stage.rstrip(b'\0').decode('utf-8'), pose_index=index,
                        roll=roll, pitch=pitch, z=z)
        finally:
            self.channel.status_lock.release()

    def close(self):
        self.memory.close()
