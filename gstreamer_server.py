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

    async with connect(signaling_server_url) as websocket:
        command = (
            "filesrc location=audiofile.mp3 "
            "! decodebin ! audioconvert ! audioresample ! opusenc ! rtpopuspay "
            "! webrtcsink bundle-policy=max-bundle"
        )
        pipeline = Gst.parse_launch(command)
        webrtc = pipeline.get_by_name("webrtcsink")

        webrtc.connect("on-negotiation-needed", on_negotiation_needed)
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
