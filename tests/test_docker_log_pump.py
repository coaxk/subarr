"""#526: the subgen log stream leaked its pump thread on client disconnect and
flooded the event loop with orphaned `Queue.put()` tasks.

`stream_subgen_logs` runs the blocking docker SDK generator on a worker
thread. Once the SSE consumer went away the thread kept running until the
container itself stopped, every further line scheduled a `q.put()` coroutine
on the loop with nobody consuming, and once the queue was full each one parked
forever. subgen's progress output produced thousands per second (#524's log:
16,000 task ids in one second, and an event-loop stall).

These tests drive the REAL DockerOps against a fake container whose stream
behaves like the SDK's: it blocks until closed, and `close()` is what makes it
stop.
"""

from __future__ import annotations

import asyncio
import threading
import time

from subarr.docker_client import DockerOps


class FakeStream:
    """Mimics docker's CancellableStream: an iterator that keeps producing
    until close() is called from another thread."""

    def __init__(self, burst: int = 0, delay: float = 0.001):
        self.closed = False
        self.produced = 0
        self.burst = burst  # lines emitted with no delay before slowing down
        self.delay = delay
        self._closed_evt = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        if self.produced >= self.burst:
            # Behave like a quiet container: block until closed or a tick passes.
            if self._closed_evt.wait(self.delay):
                raise StopIteration
        if self.closed:
            raise StopIteration
        self.produced += 1
        return f"line {self.produced}\n".encode()

    def close(self):
        self.closed = True
        self._closed_evt.set()


class FakeContainer:
    def __init__(self, stream: FakeStream):
        self.stream = stream
        self.logs_calls: list[dict] = []

    def logs(self, **kw):
        self.logs_calls.append(kw)
        return self.stream


class FakeOps(DockerOps):
    def __init__(self, stream: FakeStream):
        super().__init__()
        self.container = FakeContainer(stream)

    def _get(self):
        class _Containers:
            def get(_self, _name):
                return self.container

        class _Client:
            containers = _Containers()

        return _Client()


def _other_tasks() -> list[asyncio.Task]:
    me = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not me and not t.done()]


def test_disconnect_closes_the_container_stream_and_leaves_no_task_behind():
    stream = FakeStream()
    ops = FakeOps(stream)

    async def go():
        before = _other_tasks()
        got = []
        async for line in ops.stream_subgen_logs(tail=5):
            got.append(line)
            if len(got) == 2:
                break  # the SSE client went away
        # The pump must be told to stop by closing the SDK stream, not left to
        # run until the container exits.
        deadline = time.monotonic() + 2.0
        while not stream.closed and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert stream.closed, "the container log stream was not closed on disconnect"
        produced_at_close = stream.produced
        await asyncio.sleep(0.1)
        assert stream.produced == produced_at_close, "the pump kept reading after the consumer left"
        # Nothing orphaned on the loop: no parked Queue.put tasks, no pump task.
        await asyncio.sleep(0.05)
        leftover = [t for t in _other_tasks() if t not in before]
        assert leftover == [], f"tasks left behind after disconnect: {leftover}"
        return got

    got = asyncio.run(go())
    assert got == ["line 1", "line 2"]


def test_backpressure_drops_lines_instead_of_parking_a_task_per_line():
    # 5000 lines arrive before the consumer reads anything. The old code
    # scheduled 5000 Queue.put coroutines, 1024 of which filled the queue and
    # the rest parked forever. A log tail may LOSE lines under backpressure;
    # it may not park a task per line.
    stream = FakeStream(burst=5000)
    ops = FakeOps(stream)

    async def go():
        before = _other_tasks()
        agen = ops.stream_subgen_logs(tail=5)
        first = await agen.__anext__()
        assert first == "line 1"
        # Consumer stalls while the burst lands.
        deadline = time.monotonic() + 2.0
        while stream.produced < 5000 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert stream.produced >= 5000
        await asyncio.sleep(0.05)
        parked = [t for t in _other_tasks() if t not in before]
        # At most the pump's own worker task may be alive - never a Task per line.
        assert len(parked) <= 1, f"{len(parked)} tasks parked on the loop during backpressure"
        # Consumer resumes: it gets a bounded number of lines, then we leave.
        n = 0
        async for _line in agen:
            n += 1
            if n >= 10:
                break
        await agen.aclose()
        deadline = time.monotonic() + 2.0
        while not stream.closed and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert stream.closed

    asyncio.run(go())


def test_stream_ending_on_its_own_terminates_the_consumer():
    # A container that stops: the stream raises StopIteration by itself and
    # the consumer's `async for` must end rather than hang on the queue.
    stream = FakeStream(burst=3)
    stream.close_after = 3
    ops = FakeOps(stream)

    async def go():
        got = []

        async def consume():
            async for line in ops.stream_subgen_logs(tail=5):
                got.append(line)
                if len(got) == 3:
                    stream.close()  # simulate the container exiting
            return got

        return await asyncio.wait_for(consume(), timeout=2.0)

    assert asyncio.run(go()) == ["line 1", "line 2", "line 3"]


# #537: docker-py streams a TTY container's log one BYTE per chunk
# (`_stream_raw_result` defaults to chunk_size=1); non-TTY containers arrive
# one multiplexed frame per chunk, which may hold several lines or half of
# one. The pump reassembles on newlines so both modes yield the same lines.


class ChunkStream(FakeStream):
    """Yields a fixed list of chunks, then blocks until closed."""

    def __init__(self, chunks: list[bytes]):
        super().__init__()
        self.chunks = list(chunks)

    def __next__(self):
        # Scripted chunks are always delivered, even after close(): the
        # container wrote them before it exited. Only then does close() end
        # the stream. (Otherwise the test races the pump for the last chunk.)
        if self.chunks:
            return self.chunks.pop(0)
        if self.closed or self._closed_evt.wait(0.05):
            raise StopIteration
        return b""


def _collect(stream: ChunkStream, n: int) -> list[str]:
    ops = FakeOps(stream)

    async def go():
        got = []
        async for line in ops.stream_subgen_logs(tail=5):
            got.append(line)
            if len(got) == n:
                break
        return got

    return asyncio.run(asyncio.wait_for(go(), timeout=2.0))


def test_tty_byte_per_chunk_stream_yields_whole_lines():
    raw = b"INFO:root:Subgen v2026.08.1\nINFO:     Uvicorn running\n"
    stream = ChunkStream([bytes([b]) for b in raw])
    assert _collect(stream, 2) == ["INFO:root:Subgen v2026.08.1", "INFO:     Uvicorn running"]


def test_a_frame_holding_two_lines_yields_two_lines():
    stream = ChunkStream([b"one\ntwo\n", b"three\n"])
    assert _collect(stream, 3) == ["one", "two", "three"]


def test_a_line_split_across_frames_is_joined():
    stream = ChunkStream([b"hel", b"lo wor", b"ld\r\n", b"next\n"])
    assert _collect(stream, 2) == ["hello world", "next"]


def test_trailing_partial_line_is_delivered_when_the_stream_ends():
    stream = ChunkStream([b"complete\n", b"no newline at end"])
    ops = FakeOps(stream)

    async def go():
        got = []
        async for line in ops.stream_subgen_logs(tail=5):
            got.append(line)
            if len(got) == 1:
                stream.close()  # container exits mid-line
        return got

    assert asyncio.run(asyncio.wait_for(go(), timeout=2.0)) == ["complete", "no newline at end"]
