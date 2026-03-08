from flask import Flask
from flask_cors import CORS
from routes.auth import auth_bp
from routes.chat import chat_bp
from routes.verify_email import verify_bp
from models import init_db
import os
from dotenv import load_dotenv
from routes.paypal import paypal_bp
from routes.paddle import paddle_bp as paddle_checkout_bp
from routes.paddle_webhook import paddle_webhook
from routes.agents import agents_bp
from routes.admin import admin_bp
load_dotenv()

def create_app():
    app = Flask(__name__)

    # ✅ Explicitly allow your frontend domain
    CORS(app, origins="*", allow_headers=["Content-Type", "Authorization"], methods=["GET", "POST", "OPTIONS", "PUT", "DELETE"])

    @app.before_request
    def handle_options():
        from flask import request, Response
        if request.method == "OPTIONS":
            r = Response()
            r.headers["Access-Control-Allow-Origin"] = "*"
            r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
            return r

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
        return response

    app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "supersecretkey")
    app.config['DATABASE_URL'] = os.getenv("DATABASE_URL")

    if os.getenv("SKIP_DB_INIT") == "1":
        print("⚠️ Skipping DB init (SKIP_DB_INIT=1)")
    else:
        init_db(app)

    print("✅ paddle_checkout_bp is being registered")

    # ✅ Register Blueprints
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(chat_bp, url_prefix='/chat')
    app.register_blueprint(verify_bp, url_prefix='/verify')
    app.register_blueprint(paypal_bp, url_prefix='/paypal')
    app.register_blueprint(paddle_checkout_bp)
    app.register_blueprint(paddle_webhook)
    app.register_blueprint(agents_bp, url_prefix="/api/agents")
    app.register_blueprint(admin_bp, url_prefix='/admin')
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
