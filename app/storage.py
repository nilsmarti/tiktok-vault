from __future__ import annotations

import json
from pathlib import Path

import boto3
from botocore.config import Config

from .config import Settings


class Storage:
    def __init__(self, s: Settings):
        self.bucket = s.s3_bucket
        self.public_base = s.public_media_base_url
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put_file(self, path: Path, key: str, content_type: str) -> str:
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})
        return key

    def put_json(self, obj: dict, key: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, ContentType="application/json",
                               Body=json.dumps(obj, ensure_ascii=False, indent=2).encode())
        return key

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def public_url(self, key: str) -> str | None:
        return f"{self.public_base.rstrip('/')}/{key}" if self.public_base else None
