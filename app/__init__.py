from flask import Flask
from .webhook.routes import webhook_bp
from app.extensions import mongo

def create_app():
    app = Flask(__name__)
    app.config["MONGO_URI"] = "mongodb://localhost:27017/github_events"

    mongo.init_app(app)

    app.register_blueprint(webhook_bp, url_prefix='/webhook')


    @app.context_processor
    def utility_processor():
        from .webhook.routes import format_event_for_ui
        return dict(format_event_for_ui=format_event_for_ui)

    return app
