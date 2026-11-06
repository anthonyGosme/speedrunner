"""aws v1 or v2 by bench/s3_panel.py's protocol, on the VPS: the three commands of the
panel and the five requests to a local S3 server. The first argument is the directory of
the aws to measure, put first on PATH.

Usage: python3 vps.py <aws's directory> <speedrunner binary> <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import panel  # noqa: E402
import s3_panel  # noqa: E402

aws_dir = sys.argv.pop(1)
panel.PATH = f"{aws_dir}:{panel.PATH}"
panel.PANEL = [
    ("python", "aws --version", ["aws", "--version"], None, False),
    ("python", "aws configure list", ["aws", "configure", "list"], None, False),
    ("python", "aws ec2 run-instances --generate-cli-skeleton", ["aws", "ec2", "run-instances", "--generate-cli-skeleton"], None, False),
] + panel.PANEL
s3_panel.main()
