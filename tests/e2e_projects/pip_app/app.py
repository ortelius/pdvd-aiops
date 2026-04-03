"""Simple Flask app for E2E testing."""

from flask import Flask, jsonify, request
import click

app = Flask(__name__)


@app.route("/")
def index():
    return jsonify({"status": "ok", "message": "E2E pip test app"})


@app.route("/echo", methods=["POST"])
def echo():
    data = request.get_json()
    return jsonify({"echo": data})


@app.route("/health")
def health():
    return jsonify({"healthy": True})


@click.command()
@click.option("--port", default=5000)
def run(port):
    app.run(port=port)


if __name__ == "__main__":
    run()
