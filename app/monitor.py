from flask import Flask, Response
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST
import psutil

app = Flask(__name__)

cpu_percent = Gauge(
    "custom_cpu_percent",
    "Current CPU usage percentage"
)

ram_percent = Gauge(
    "custom_ram_percent",
    "Current RAM usage percentage"
)

ram_used_gb = Gauge(
    "custom_ram_used_gb",
    "Current RAM used in GB"
)


def collect_metrics():
    memory = psutil.virtual_memory()

    cpu_percent.set(psutil.cpu_percent(interval=0.1))
    ram_percent.set(memory.percent)
    ram_used_gb.set(memory.used / (1024 ** 3))


@app.route("/metrics")
def metrics():
    collect_metrics()
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/")
def health():
    return "custom monitor exporter is running\n"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9101)