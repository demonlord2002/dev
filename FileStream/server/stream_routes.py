import os
import time
import math
import logging
import mimetypes
import traceback
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine

from FileStream.bot import multi_clients, work_loads, FileStream
from FileStream.config import Telegram, Server
from FileStream.server.exceptions import FIleNotFound, InvalidHash
from FileStream import utils, StartTime, __version__
from FileStream.utils.render_template import render_page

routes = web.RouteTableDef()
class_cache = {}

# --------------------- STATUS ROUTE --------------------- #
@routes.get("/status", allow_head=True)
async def status_handler(_):
    return web.json_response({
        "server_status": "running",
        "uptime": utils.get_readable_time(time.time() - StartTime),
        "telegram_bot": "@" + FileStream.username,
        "connected_bots": len(multi_clients),
        "loads": {f"bot{c+1}": l for c, (_, l) in enumerate(sorted(work_loads.items(), key=lambda x: x[1], reverse=True))},
        "version": __version__
    })

# --------------------- WATCH ROUTE --------------------- #
@routes.get("/watch/{file_id}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        file_id = request.match_info["file_id"]
        html = await render_page(file_id)
        return web.Response(text=html, content_type="text/html")
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass

# --------------------- DOWNLOAD ROUTE --------------------- #
@routes.get("/dl/{file_id}", allow_head=True)
async def download_handler(request: web.Request):
    try:
        file_id = request.match_info["file_id"]
        return await media_streamer(request, file_id)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass
    except Exception as e:
        logging.error(f"Download error: {e}")
        traceback.print_exc()
        raise web.HTTPInternalServerError(text=str(e))

# --------------------- MEDIA STREAMER --------------------- #
async def media_streamer(request: web.Request, db_id: str):
    range_header = request.headers.get("Range", None)

    # pick the bot with lowest workload
    index = min(work_loads, key=work_loads.get)
    client = multi_clients[index]

    if Telegram.MULTI_CLIENT:
        logging.info(f"Client {index} serving {request.headers.get('X-FORWARDED-FOR', request.remote)}")

    # reuse ByteStreamer object
    if client in class_cache:
        streamer = class_cache[client]
    else:
        streamer = utils.ByteStreamer(client)
        class_cache[client] = streamer

    # fetch file metadata
    file_obj = await streamer.get_file_properties(db_id, multi_clients)
    file_size = file_obj.file_size

    # parse Range header safely
    if range_header:
        try:
            from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
            from_bytes = int(from_bytes)
            until_bytes = int(until_bytes) if until_bytes else file_size - 1
        except:
            from_bytes = 0
            until_bytes = file_size - 1
    else:
        from_bytes = 0
        until_bytes = file_size - 1

    # validate range
    if from_bytes < 0 or until_bytes >= file_size or until_bytes < from_bytes:
        return web.Response(
            status=416,
            body="416: Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    # chunk setup
    chunk_size = 1024 * 1024
    offset = from_bytes - (from_bytes % chunk_size)
    first_cut = from_bytes - offset
    last_cut = until_bytes % chunk_size + 1
    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)

    body = streamer.yield_file(file_obj, index, offset, first_cut, last_cut, part_count, chunk_size)

    mime_type = file_obj.mime_type or mimetypes.guess_type(utils.get_name(file_obj))[0] or "application/octet-stream"
    file_name = utils.get_name(file_obj)

    disposition = "attachment"

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers={
            "Content-Type": mime_type,
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(until_bytes - from_bytes + 1),
            "Content-Disposition": f'{disposition}; filename="{file_name}"',
            "Accept-Ranges": "bytes"
        }
    )

# --------------------- APP RUNNER --------------------- #
def start_server():
    app = web.Application(client_max_size=300000000)  # increased max size for big files
    app.add_routes(routes)
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    start_server()
