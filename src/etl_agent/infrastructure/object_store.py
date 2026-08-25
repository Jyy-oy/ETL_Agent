"""MinIO/S3 对象存储抽象和开发实现。"""

import asyncio
from typing import BinaryIO, Protocol

import boto3

from etl_agent.config import Settings


class ObjectStoreError(RuntimeError):
    """表示对象上传或删除失败。"""


class ObjectStore(Protocol):
    """文件对象存储的最小异步端口。"""

    async def put(
        self,
        fileobj: BinaryIO,
        object_key: str,
        size_bytes: int,
        content_type: str,
    ) -> None:
        """上传文件对象并设置内容类型。"""

    async def delete(self, object_key: str) -> None:
        """删除指定对象，用于数据库提交失败后的补偿清理。"""


class MinioObjectStore:
    """通过 boto3 S3 API 访问 MinIO，不把对象内容载入进程内存。"""

    def __init__(self, settings: Settings) -> None:
        """根据 MinIO 配置创建 S3 客户端。"""
        self.bucket = settings.minio_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint,
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            region_name=settings.minio_region,
            use_ssl=settings.minio_secure,
        )

    async def put(
        self,
        fileobj: BinaryIO,
        object_key: str,
        size_bytes: int,
        content_type: str,
    ) -> None:
        """在线程中执行流式上传，失败时隐藏 SDK 细节。"""
        try:
            await asyncio.to_thread(
                self.client.upload_fileobj,
                fileobj,
                self.bucket,
                object_key,
                ExtraArgs={"ContentType": content_type},
            )
        except Exception as exc:
            raise ObjectStoreError("对象上传失败") from exc

    async def delete(self, object_key: str) -> None:
        """在线程中删除对象，失败时转换为稳定存储错误。"""
        try:
            await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=object_key)
        except Exception as exc:
            raise ObjectStoreError("对象删除失败") from exc


def create_object_store(settings: Settings) -> ObjectStore:
    """创建当前环境使用的对象存储适配器。"""
    return MinioObjectStore(settings)
