from aiohttp import web
from .stream_routes import routes

def web_server():
    app = web.Application(client_max_size=300000000)
    app.add_routes(routes)
    return app

if __name__ == "__main__":
    app = web_server()
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)
    
