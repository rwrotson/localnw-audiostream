import os

os.environ["GI_TYPELIB_PATH"] = "/Library/Frameworks/GStreamer.framework/Versions/Current/lib/gstreamer-1.0"
os.environ["LD_LIBRARY_PATH"] = "/Library/Frameworks/GStreamer.framework/Versions/Current/lib"

import asyncio
from argparse import ArgumentParser

from gi import require_version

require_version("Gst", "1.0")
require_version("GstWebRTC", "1.0")
from gi.repository import Gst, GstWebRTC  # noqa
from websockets import connect


def init_gst():
    Gst.init(None)
    print(f"{Gst.version_string()} initialized!")


async def setup_webrtc(signaling_server_url: str):
    def on_negotiation_needed(sink: GstWebRTC):
        print("Negotiation needed")
        offer = sink.emit("create-offer")
        print("Created offer:", offer)
        asyncio.create_task(websocket.send(offer.sdp))

    # Signal handlers that are called from webrtcsink
    def signaller_on_start(_):
        print("starting")
        asyncio.run_coroutine_threadsafe(self.connect(), self.event_loop)
        return True

    def signaller_on_stop(self, _):
        print("stopping")
        self.event_loop.stop()
        return True

    def signaller_on_send_session_description(self, _, session_id, offer):
        typ = "offer"
        if offer.type == GstWebRTC.WebRTCSDPType.ANSWER:
            typ = "answer"
        sdp = offer.sdp.as_text()
        self.send_soon({"type": "peer", "sessionId": session_id, "sdp": {"type": typ, "sdp": sdp}})
        return True

    def signaller_on_send_ice(self, _, session_id, candidate, sdp_m_line_index, sdp_mid):
        self.send_soon(
            {
                "type": "peer",
                "sessionId": session_id,
                "ice": {"candidate": candidate, "sdpMLineIndex": sdp_m_line_index},
            }
        )
        return True

    def signaller_on_end_session(self, _, session_id):
        self.send_soon({"type": "endSession", "sessionId": session_id})
        return True

    def signaller_on_consumer_added(self, _, peer_id, webrtcbin):
        pass

    def signaller_on_consumer_removed(self, _, peer_id, webrtcbin):
        pass

    def signaller_on_webrtcbin_ready(self, _, peer_id, webrtcbin):
        pass

    # Signals we have to emit to notify webrtcsink
    def signaller_emit_error(self, error):
        self.signaller.emit("error", error)

    def signaller_emit_request_meta(self):
        meta = self.signaller.emit("request-meta")
        return meta

    def signaller_emit_session_requested(self, session_id, peer_id, offer):
        self.signaller.emit("session-requested", session_id, peer_id, offer)

    def signaller_emit_session_description(self, session_id, sdp):
        res, sdp = GstSdp.SDPMessage.new_from_text(sdp)
        answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdp)
        self.signaller.emit("session-description", session_id, answer)

    def signaller_emit_handle_ice(self, session_id, sdp_m_line_index, sdp_mid, candidate):
        self.signaller.emit("handle-ice", session_id, sdp_m_line_index, sdp_mid, candidate)

    def signaller_emit_session_ended(self, session_id):
        return self.signaller.emit("session-ended", session_id)

    def signaller_emit_shutdown():
        self.signaller.emit("shutdown")

    async with connect(signaling_server_url) as websocket:
        command = (
            "filesrc location=/Users/macbook/Downloads/test.mp3 "
            "! decodebin "
            "! audioconvert "
            "! audioresample "
            "! opusenc "
            "! webrtcsink name=sink"
        )
        pipeline = Gst.parse_launch(command)

        webrtc = pipeline.get_by_name("sink")
        signaller = webrtc.get_property("signaller")
        signaller.connect("start", on_negotiation_needed)
        pipeline.set_state(Gst.State.PLAYING)

        print("Pipeline playing")

        await websocket.recv()  # Wait for signaling messages (e.g., answer/ICE candidates)


async def main():
    parser = ArgumentParser(description="Run a GStreamer-based WebRTC client.")
    parser.add_argument("--server", type=str, default="ws://localhost:8080", help="Signaling server URL.")
    args = parser.parse_args()

    init_gst()

    await setup_webrtc(signaling_server_url=args.server)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")
