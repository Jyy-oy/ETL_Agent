"""SecretProvider 抽象和 Vault KV v2 开发实现。"""

import asyncio
from collections.abc import Mapping
from typing import Protocol

import hvac
from hvac.exceptions import InvalidPath

from etl_agent.config import Settings


class SecretProviderError(RuntimeError):
    """表示 SecretRef 无法解析或 SecretProvider 不可用。"""


class SecretProvider(Protocol):
    """运行时读取连接凭据的最小抽象。"""

    async def read(self, secret_ref: str) -> Mapping[str, str]:
        """按引用读取非空字符串凭据映射。"""


def normalize_vault_path(secret_ref: str, prefix: str, mount: str) -> str:
    """把外部 SecretRef 规范化为 Vault KV v2 的相对路径并阻止路径穿越。"""
    value = secret_ref.strip().strip("/")
    mount_name = mount.strip().strip("/")
    if value.startswith(f"{mount_name}/"):
        value = value[len(mount_name) + 1 :]
    if value.startswith("data/"):
        value = value[5:]
    if not value or ".." in value.split("/") or "://" in value:
        raise SecretProviderError("SecretRef 路径无效")
    prefix_value = prefix.strip().strip("/")
    if prefix_value and value != prefix_value and not value.startswith(f"{prefix_value}/"):
        value = f"{prefix_value}/{value}"
    return value


class VaultSecretProvider:
    """使用 Vault KV v2 读取连接凭据，调用在工作线程中执行以避免阻塞事件循环。"""

    def __init__(self, settings: Settings) -> None:
        """根据应用配置创建 Vault 客户端，不在初始化阶段读取任何 Secret。"""
        self.settings = settings
        self.client = hvac.Client(
            url=settings.vault_addr,
            token=settings.vault_token,
            namespace=settings.vault_namespace or None,
        )

    async def read(self, secret_ref: str) -> Mapping[str, str]:
        """读取并校验 Vault KV v2 数据，错误响应不暴露凭据内容。"""
        path = normalize_vault_path(
            secret_ref,
            prefix=self.settings.vault_kv_prefix,
            mount=self.settings.vault_kv_mount,
        )
        try:
            response = await asyncio.to_thread(
                self.client.secrets.kv.v2.read_secret_version,
                path=path,
                mount_point=self.settings.vault_kv_mount,
            )
        except InvalidPath as exc:
            raise SecretProviderError("SecretRef 不存在") from exc
        except Exception as exc:
            raise SecretProviderError("SecretProvider 不可用") from exc
        data = response.get("data", {}).get("data", {})
        if not isinstance(data, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in data.items()
        ):
            raise SecretProviderError("Secret 内容格式无效")
        return data


def create_secret_provider(settings: Settings) -> SecretProvider:
    """根据配置创建 SecretProvider，未批准的 Provider 名称直接拒绝。"""
    if settings.secret_provider.lower() != "vault":
        raise ValueError(f"不支持的 SecretProvider: {settings.secret_provider}")
    return VaultSecretProvider(settings)
