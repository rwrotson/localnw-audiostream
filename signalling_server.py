import asyncio
from argparse import ArgumentParser

from websockets.asyncio.server import serve, ServerConnection

clients: set[ServerConnection] = set()


async def signaling_server(websocket: ServerConnection) -> None:
    """Handles a single WebSocket connection for the signaling server."""
    clients.add(websocket)
    try:
        async for message in websocket:
            for client in clients:
                if client != websocket:
                    await client.send(message)
    finally:
        clients.remove(websocket)


async def main() -> None:
    """Entry point for the signaling server. Parses arguments and starts the server."""
    parser = ArgumentParser(description="Run a WebSocket-based signaling server.")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address to bind to.")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to.")
    args = parser.parse_args()

    stop = asyncio.get_running_loop().create_future()
    async with serve(signaling_server, args.host, args.port):
        print(f"Signaling server running on ws://{args.host}:{args.port}")
        await stop  # Keep the server running indefinitely


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")
