"""Bytes to and from the Cameo: Bluetooth LE, USB, or a recording stand-in for dry runs.

Every transport speaks the same small interface, reply-oriented because the machine answers
queries with ETX-terminated strings: ``write`` sends bytes, ``reply`` waits for the next
complete reply, ``drain`` discards (and returns) anything unread. Everything that crosses the
wire, and every movement event the BLE link reports on its side channel, goes to the session
log with a timestamp — the log is how we learn what the machine actually does.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable
from typing import ClassVar, Protocol

ETX = b"\x03"
INIT = b"\x1b\x04"  # ESC EOT: the handshake the machine expects on every BLE characteristic

Log = Callable[..., None]  # log(event, **fields)


class TransportError(OSError):
    pass


class Transport(Protocol):
    name: str

    def write(self, data: bytes) -> None: ...
    def reply(self, timeout: float) -> bytes | None: ...
    def drain(self) -> list[bytes]: ...
    def close(self) -> None: ...


class _Replies:
    """ETX-framed reply buffer shared by the stream transports."""

    def __init__(self, log: Log):
        self.log = log
        self.buf = bytearray()
        self.cond = threading.Condition()
        self.dead: str | None = None

    def feed(self, data: bytes) -> None:
        with self.cond:
            self.buf.extend(data)
            self.cond.notify_all()
        self.log("rx", data=data)

    def kill(self, why: str) -> None:
        with self.cond:
            self.dead = why
            self.cond.notify_all()

    def take(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                i = self.buf.find(ETX)
                if i >= 0:
                    out = bytes(self.buf[: i + 1])
                    del self.buf[: i + 1]
                    return out
                if self.dead:
                    raise TransportError(self.dead)
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.cond.wait(left)

    def clear(self) -> list[bytes]:
        with self.cond:
            parts = [p + ETX for p in bytes(self.buf).split(ETX) if p]
            self.buf.clear()
        return parts


class BleTransport:
    """The Cameo's vendor GATT service. Commands go to WRITE in acknowledged 20-byte writes,
    replies arrive on READ, and CONTROL carries movement events (logged, never mixed into
    replies — a scan's own "moving" event must not be mistaken for its result)."""

    SERVICE = "e2088282-4fde-42f9-bb22-6ec3c7ed8f91"
    WRITE = "6d92661d-f429-4d67-929b-28e7a9780912"
    READ = "8dcf199a-30e7-4bd4-beb6-beb57dca866c"
    CONTROL = "61490654-b5b4-458c-a867-9e15bc1471e0"
    CHUNK = 20  # until the link reports its MTU
    MAX_CHUNK = 128  # Studio writes up to 137 bytes at once; stay below what it has proven
    SETTLE_S = 1.0

    def __init__(self, log: Log):
        self.log = log
        self.name = "ble"
        self.replies = _Replies(log)
        self.client = None
        self.chunk = self.CHUNK
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="cameo-ble", daemon=True)
        self.thread.start()

    def _run(self, coro, timeout: float):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout)
        except TimeoutError as e:
            fut.cancel()
            raise TransportError("Bluetooth LE operation timed out") from e

    @staticmethod
    def discover(timeout: float = 8.0) -> list[tuple[str, str]]:
        """(address, advertised name) of every BLE device in range, named ones first."""
        from bleak import BleakScanner

        found = asyncio.run(BleakScanner.discover(timeout=timeout, return_adv=True))
        out = [(dev.address, adv.local_name or dev.name or "") for dev, adv in found.values()]
        return sorted(out, key=lambda r: (not r[1], r[1].casefold(), r[0]))

    @classmethod
    def connect(cls, name: str, log: Log, timeout: float = 20.0) -> BleTransport:
        t = cls(log)
        try:
            t._run(t._connect(name, timeout), timeout * 2 + 10)
        except BaseException:
            t.close()
            raise
        return t

    async def _connect(self, name: str, timeout: float) -> None:
        from bleak import BleakClient, BleakScanner

        want = name.casefold()
        seen: dict[str, str] = {}

        def match(dev, adv) -> bool:
            n = (adv.local_name or dev.name or "").casefold()
            if n:
                seen[dev.address] = n
            return bool(n) and (n == want or want in n)

        dev = await BleakScanner.find_device_by_filter(match, timeout=timeout)
        if dev is None:
            raise TransportError(f"no Bluetooth LE device named like {name!r} (is the Cameo on and free?)")
        self.log("ble_found", name=dev.name, address=dev.address)
        client = BleakClient(dev, disconnected_callback=lambda _c: self.replies.kill("Cameo disconnected"))
        await client.connect()
        self.client = client
        if client.services.get_service(self.SERVICE) is None:
            raise TransportError(f"{dev.name} has no Silhouette service {self.SERVICE}")
        await client.start_notify(self.CONTROL, lambda _c, d: self.log("event", data=bytes(d)))
        await client.start_notify(self.READ, lambda _c, d: self.replies.feed(bytes(d)))
        for char in (self.CONTROL, self.READ, self.WRITE):
            await client.write_gatt_char(char, INIT, response=True)
        await asyncio.sleep(self.SETTLE_S)
        # One write request carries MTU − 3 bytes (the link negotiates 185 with this machine).
        self.chunk = max(self.CHUNK, min(client.mtu_size - 3, self.MAX_CHUNK))
        self.name = f"ble {dev.name}"
        self.log(
            "connected", via=self.name, mtu=client.mtu_size, chunk=self.chunk, dropped=self.replies.clear()
        )

    def write(self, data: bytes) -> None:
        self.log("tx", data=data)
        for i in range(0, len(data), self.chunk):
            self._run(self._write(data[i : i + self.chunk]), 10.0)

    async def _write(self, chunk: bytes) -> None:
        for attempt in range(4):
            try:
                await self.client.write_gatt_char(self.WRITE, chunk, response=True)
                return
            except Exception as e:  # the machine's ATT "busy" (0x0e) is transient backpressure
                if "0x0e" not in str(e).casefold() or attempt == 3:
                    raise
                await asyncio.sleep(0.1 * 2**attempt)

    def reply(self, timeout: float) -> bytes | None:
        return self.replies.take(timeout)

    def drain(self) -> list[bytes]:
        return self.replies.clear()

    def close(self) -> None:
        if self.client is not None and self.client.is_connected:
            with contextlib.suppress(Exception):  # closing is best-effort
                self._run(self.client.disconnect(), 5.0)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5.0)
        self.log("closed")


class UsbTransport:
    """libusb bulk endpoints (0x01 out, 0x82 in); a reader thread feeds the reply buffer."""

    IDS: ClassVar = ((0x3844, 0x0001), (0x3844, 0x0002))  # Cameo 5 Alpha, Alpha Plus
    OUT, IN = 0x01, 0x82

    def __init__(self, log: Log):
        import usb1

        self.log = log
        self.replies = _Replies(log)
        self.ctx = usb1.USBContext()
        self.handle = None
        for vid, pid in self.IDS:
            self.handle = self.ctx.openByVendorIDAndProductID(vid, pid)
            if self.handle is not None:
                break
        if self.handle is None:
            self.ctx.close()
            raise TransportError("no Cameo 5 Alpha on USB (is the cable plugged in and the machine on?)")
        self.handle.claimInterface(0)
        self.name = "usb"
        self.stop = threading.Event()
        self.reader = threading.Thread(target=self._read_loop, name="cameo-usb", daemon=True)
        self.reader.start()
        log("connected", via=self.name)

    def _read_loop(self) -> None:
        import usb1

        while not self.stop.is_set():
            try:
                data = self.handle.bulkRead(self.IN, 64, timeout=200)
            except usb1.USBErrorTimeout:
                continue
            except usb1.USBError as e:
                self.replies.kill(f"USB read failed: {e}")
                return
            if data:
                self.replies.feed(bytes(data))

    def write(self, data: bytes) -> None:
        self.log("tx", data=data)
        self.handle.bulkWrite(self.OUT, data, timeout=10000)

    def reply(self, timeout: float) -> bytes | None:
        return self.replies.take(timeout)

    def drain(self) -> list[bytes]:
        return self.replies.clear()

    def close(self) -> None:
        self.stop.set()
        self.reader.join(2.0)
        self.handle.releaseInterface(0)
        self.handle.close()
        self.ctx.close()
        self.log("closed")


class RecordingTransport:
    """A dry run's machine: records every byte and answers from ``answers``, command prefix →
    reply, or → a list of replies used in turn (the last one repeats)."""

    def __init__(self, log: Log, answers: dict[bytes, bytes | list[bytes]]):
        self.log = log
        self.name = "dry run"
        self.answers = {k: list(v) if isinstance(v, list) else [v] for k, v in answers.items()}
        self.sent = bytearray()
        self.pending: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.log("tx", data=data)
        self.sent.extend(data)
        for cmd in data.split(ETX):
            for prefix, queue in self.answers.items():
                if cmd.startswith(prefix):
                    self.pending.append(queue.pop(0) if len(queue) > 1 else queue[0])
                    break

    def reply(self, timeout: float) -> bytes | None:
        out = self.pending.pop(0) if self.pending else None
        if out is not None:
            self.log("rx", data=out)
        return out

    def drain(self) -> list[bytes]:
        out, self.pending = self.pending, []
        return out

    def close(self) -> None:
        self.log("closed")
