#!/usr/bin/env python3
# Reference:
# https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs/-/blob/main/net/webrtc/examples/webrtcsink-custom-signaller.py

import websockets
import asyncio
import json
from argparse import ArgumentParser
from asyncio import AbstractEventLoop
from dataclasses import dataclass
from typing import Any

from gi import require_version
from websockets.asyncio.server import ServerConnection

require_version("Gst", "1.0")
require_version("GstSdp", "1.0")
require_version("GstWebRTC", "1.0")

from gi.repository import Gst  # noqa
from gi.repository import GstSdp  # noqa
from gi.repository import GstWebRTC  # noqa

# Ensure that gst-python is installed
try:
    from gi.overrides import Gst as _
except ImportError:
    print("gstreamer-python binding overrides aren't available, please install them")
    raise


type Bus = Gst.Bus
type Element = Gst.Element
type Message = Gst.Message

SessionDescription = GstWebRTC.WebRTCSessionDescription
MessageDirection = GstWebRTC.WebRTCSDPType


def returns_true(func):
    """Decorator for signal handlers to ensure they return True."""

    def wrapper(*args, **kwargs):
        func(*args, **kwargs)
        return True

    return wrapper


class NoConnectionError(Exception):
    """Raised when a connection is needed but not available."""

    pass


class WebRTCClient:
    def __init__(self, event_loop: AbstractEventLoop, server: str, command: str):
        self._connection: ServerConnection | None = None
        self.pipe: Element = Gst.parse_launch(command)
        self.sink = self.pipe.get_by_name("sink")
        self.signaller = self.sink.get_property("signaller")
        self.event_loop = event_loop
        self._server = server

        self._setup_pipeline()
        self._register_signal_handlers()
        self.pipe.set_state(Gst.State.PLAYING)

    @property
    def connection(self) -> ServerConnection:
        if not (connection := self._connection):
            raise NoConnectionError("Not connected")
        return connection

    @connection.setter
    def connection(self, value: ServerConnection) -> None:
        self._connection = value

    def _setup_pipeline(self):
        """Set up GStreamer pipeline and bus monitoring."""
        bus = self.pipe.get_bus()
        poll_fd = bus.get_pollfd().fd
        callback = self._on_bus_poll
        self.event_loop.add_reader(poll_fd, callback, bus)

    def _stop_pipeline(self) -> None:
        """Stop the GStreamer pipeline and event loop."""
        bus = self.pipe.get_bus()
        poll_fd = bus.get_pollfd().fd
        self.event_loop.remove_reader(poll_fd)
        self.event_loop.stop()
        self.pipe.set_state(Gst.State.NULL)

    def _register_signal_handlers(self) -> None:
        """Register signal handlers for WebRTC events."""
        handlers = {
            "start": self._on_start,
            "stop": self._on_stop,
            "send-session-description": self._on_send_session_description,
            "send-ice": self._on_send_ice,
            "end-session": self._on_end_session,
            "consumer-added": self._on_consumer_added,
            "consumer-removed": self._on_consumer_removed,
            "webrtcbin-ready": self._on_webrtcbin_ready,
        }
        for signal, handler in handlers.items():
            self.signaller.connect(signal, handler)

    def _on_bus_poll(self, bus: Bus) -> None:
        """Callback to handle messages from the GStreamer bus.

        This method is invoked whenever there is activity on the GStreamer bus's
        file descriptor. It processes messages such as errors, end-of-stream (EOS),
        or latency-related events. If a terminal condition (e.g., error or EOS) is
        detected, the bus is removed from the event loop, and the loop is stopped.
        """

        def handle_error(msg: Message) -> None:
            """Handles an error message from the bus."""
            err = msg.parse_error()
            print(f"ERROR: {err.gerror} -- {err.debug}")
            self._stop_pipeline()

        while msg := bus.pop():
            match msg.type:
                case Gst.MessageType.ERROR:
                    handle_error(msg)
                case Gst.MessageType.EOS:
                    self._setup_pipeline()
                case Gst.MessageType.LATENCY:
                    self.pipe.recalculate_latency()

    async def send(self, msg: dict[str, Any]) -> None:
        json_msg = json.dumps(msg)
        print(f">>> {json_msg}")
        await self.connection.send(json_msg)

    def send_soon(self, msg: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(
            coro=self.send(msg),
            loop=self.event_loop,
        )

    async def connect(self):
        """Connect to the WebSocket signalling server."""
        print(f"Connecting to {self._server}...")
        self.connection = await websockets.connect(self._server)
        try:
            async for message in self.connection:
                await self._handle_message(message)
        finally:
            self.connection = None
            self._stop_pipeline()

    async def _handle_message(self, msg_json: str) -> None:
        print(f"<<< {msg_json}")

        try:
            msg = json.loads(msg_json)
        except json.decoder.JSONDecodeError:
            print("Failed to parse JSON message, this might be a bug")
            raise

        match msg.get("type"):
            case "welcome":
                self.peer_id = msg["peerId"]
                print(f"Got peer ID {self.peer_id} assigned")
                msg = {
                    "type": "setPeerStatus",
                    "roles": ["producer"],
                    "meta": self.emit_request_meta(),
                }
                await self.send(msg)

            case "sessionStarted":
                pass

            case "startSession":
                offer = None
                if msg["offer"]:
                    _, sdpmsg = GstSdp.SDPMessage.new()
                    GstSdp.sdp_message_parse_buffer(bytes(msg["offer"].encode()), sdpmsg)
                    offer = SessionDescription.new(MessageDirection.OFFER, sdpmsg)
                self.emit_session_requested(msg["sessionId"], msg["peerId"], offer)
            case "endSession":
                self.emit_session_ended(msg["sessionId"])

            case "peer":
                if "sdp" in msg:
                    sdp = msg["sdp"]
                    assert sdp["type"] == "answer"
                    self.emit_session_description(msg["sessionId"], sdp["sdp"])
                elif "ice" in msg:
                    ice = msg["ice"]
                    self.emit_handle_ice(msg["sessionId"], ice["sdpMLineIndex"], None, ice["candidate"])
                else:
                    print("unknown peer message")

            case "error":
                self.emit_error(f"Error message from server {msg['details']}")
            case _:
                print("unknown message type")

    async def stop(self) -> None:
        if self.connection:
            await self.connection.close()
        self.connection = None

    @returns_true
    def _on_start(self, _: Element) -> None:
        print("starting...")
        asyncio.run_coroutine_threadsafe(self.connect(), self.event_loop)

    @returns_true
    def _on_stop(self, _: Element) -> None:
        print("stopping...")
        self.event_loop.stop()

    @returns_true
    def _on_send_session_description(self, _: Element, session_id: str, offer: SessionDescription) -> None:
        msg = {
            "type": "peer",
            "sessionId": session_id,
            "sdp": {
                "type": "answer" if offer.type == MessageDirection.ANSWER else "offer",
                "sdp": offer.sdp.as_text(),
            },
        }
        self.send_soon(msg)

    @returns_true
    def _on_send_ice(
        self, _: Element, session_id: str, candidate: str, sdp_m_line_index: int, sdp_mid: str | None
    ) -> None:
        msg = {
            "type": "peer",
            "sessionId": session_id,
            "ice": {
                "candidate": candidate,
                "sdpMLineIndex": sdp_m_line_index,
            },
        }
        self.send_soon(msg)

    @returns_true
    def _on_end_session(self, _: Element, session_id: str) -> None:
        msg = {
            "type": "endSession",
            "sessionId": session_id,
        }
        self.send_soon(msg)

    def _on_consumer_added(self, _: Element, peer_id: str, webrtcbin: Element) -> None:
        pass

    def _on_consumer_removed(self, _: Element, peer_id: str, webrtcbin: Element) -> None:
        pass

    def _on_webrtcbin_ready(self, _: Element, peer_id: str, webrtcbin: Element) -> None:
        pass

    def emit_error(self, error: str) -> None:
        self.signaller.emit("error", error)

    def emit_request_meta(self) -> Any:
        meta = self.signaller.emit("request-meta")
        return meta

    def emit_session_requested(self, session_id: str, peer_id: str, offer: SessionDescription | None) -> None:
        self.signaller.emit("session-requested", session_id, peer_id, offer)

    def emit_session_description(self, session_id: str, sdp: str) -> None:
        res, sdp = GstSdp.SDPMessage.new_from_text(sdp)
        answer = SessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdp)
        self.signaller.emit("session-description", session_id, answer)

    def emit_handle_ice(self, session_id: str, sdp_m_line_index: int, sdp_mid: str | None, candidate: str) -> None:
        self.signaller.emit("handle-ice", session_id, sdp_m_line_index, sdp_mid, candidate)

    def emit_session_ended(self, session_id: str) -> Any:
        return self.signaller.emit("session-ended", session_id)

    def emit_shutdown(self) -> None:
        self.signaller.emit("shutdown")


DEFAULT_SIGNALLING_SERVER = "ws://0.0.0.0:8443"
DEFAULT_COMMAND_ = (
    "webrtcsink name=sink do-fec=false videotestsrc is-live=true "
    "! video/x-raw,width=800,height=600 "
    "! sink. audiotestsrc is-live=true "
    "! sink."
)
DEFAULT_COMMAND = (
    "filesrc location=/Users/macbook/Downloads/test.mp3 "
    "! decodebin "
    "! audioconvert "
    "! audioresample "
    "! opusenc "
    "! webrtcsink name=sink"
)


@dataclass(frozen=True, slots=True)
class ScriptArgs:
    server: str
    command: str


def parse_args() -> ScriptArgs:
    parser = ArgumentParser(description="Run a GStreamer-based WebRTC client.")
    parser.add_argument(
        "--server",
        default=DEFAULT_SIGNALLING_SERVER,
        help=f"Signalling server to connect to, e.g.: {DEFAULT_SIGNALLING_SERVER}",
    )
    parser.add_argument(
        "--command",
        default=DEFAULT_COMMAND,
        help=f"GStreamer pipeline to run, e.g.: {DEFAULT_COMMAND}",
    )
    args = parser.parse_args()

    return ScriptArgs(server=args.server, command=args.command)


def main() -> None:
    args = parse_args()
    loop = asyncio.new_event_loop()
    client = WebRTCClient(
        event_loop=loop,
        server=args.server,
        command=args.command,
    )
    try:
        loop.run_forever()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
