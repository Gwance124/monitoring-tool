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

ram_used_bytes = Gauge(
    "custom_ram_used_bytes",
    "Current RAM used in bytes"
)

ram_total_bytes = Gauge(
    "custom_ram_total_bytes",
    "Total RAM in bytes"
)

ram_available_bytes = Gauge(
    "custom_ram_available_bytes",
    "Available RAM in bytes"
)

def collect_metrics():
    memory = psutil.virtual_memory()

    cpu_percent.set(psutil.cpu_percent(interval=0.1))
    ram_percent.set(memory.percent)
    ram_used_bytes.set(memory.used)
    ram_total_bytes.set(memory.total)
    ram_available_bytes.set(memory.available)


@app.route("/metrics")
def metrics():
    collect_metrics()
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/")
def health():
    return "custom monitor exporter is running\n"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9101)