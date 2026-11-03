"""aws against S3, measured as panel.py measures it, on the VPS: a local S3
server (moto) answers, so the network takes next to no time and the
difference is speedrunner's. Against AWS, the network latency adds the same
time to both columns: the time saved stays, the ratio drops.

Usage: python3 s3_panel.py <speedrunner binary> <out_dir>   (env as panel.py)
Env:   MOTO (moto_server's path), S3_PORT (default 5056).
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel  # noqa: E402

ROOT = panel.ROOT
MOTO = os.environ.get("MOTO", f"{ROOT}/venv-moto/bin/moto_server")
PORT = os.environ.get("S3_PORT", "5056")
ENDPOINT = f"http://127.0.0.1:{PORT}"
BUCKET = "speedrunner-bench"

panel.SAMPLES.update({"sample.txt": "hello\n"})
# Test-only keys for the local server.
panel.EXTRA_ENV = {
    "AWS_ENDPOINT_URL": ENDPOINT, "AWS_DEFAULT_REGION": "eu-west-3",
    "AWS_ACCESS_KEY_ID": "local-test", "AWS_SECRET_ACCESS_KEY": "local-test",
}

panel.PANEL = [
    ("python", "aws s3 ls (local S3)", ["aws", "s3", "ls"], None, False),
    ("python", f"aws s3 ls s3://{BUCKET}/ (local S3)", ["aws", "s3", "ls", f"s3://{BUCKET}/"], None, False),
    ("python", "aws s3 cp, a small file (local S3)",
     ["aws", "s3", "cp", "--no-progress", "sample.txt", f"s3://{BUCKET}/upload.txt"], "work", False),
    ("python", "aws s3api head-object (local S3)",
     ["aws", "s3api", "head-object", "--bucket", BUCKET, "--key", "a.txt"], None, False),
    ("python", "aws sts get-caller-identity (local)", ["aws", "sts", "get-caller-identity"], None, False),
]


def aws(*args):
    env = {**os.environ, **panel.EXTRA_ENV, "PATH": panel.PATH, "AWS_EC2_METADATA_DISABLED": "true",
           "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull}
    return subprocess.run(["aws", *args], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    server = subprocess.Popen([MOTO, "-H", "127.0.0.1", "-p", PORT],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while aws("s3", "mb", f"s3://{BUCKET}").returncode != 0:
            if time.monotonic() > deadline:
                sys.exit("moto did not start")
            time.sleep(0.2)
        for key in ("a.txt", "b.txt", "logs/c.txt"):
            aws("s3api", "put-object", "--bucket", BUCKET, "--key", key)
        panel.main()
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
