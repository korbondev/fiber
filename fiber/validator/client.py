import json
from typing import Any, AsyncGenerator

import httpx
from cryptography.fernet import Fernet

from fiber import constants as bcst
from fiber import constants as cst
from fiber.chain.models import Node
from fiber.logging_utils import get_logger
from fiber.validator.generate_nonce import generate_nonce

logger = get_logger(__name__)


def _get_headers(symmetric_key_uuid: str, validator_ss58_address: str) -> dict[str, str]:
    return {
        "Content-Type": "application/octet-stream",  # NOTE: Good?
        bcst.SYMMETRIC_KEY_UUID: symmetric_key_uuid,
        bcst.SS58_ADDRESS: validator_ss58_address,
    }


def construct_server_address(
    node: Node,
    replace_with_docker_localhost: bool = False,
    replace_with_localhost: bool = False,
) -> str:
    """
    Currently just supports http4.
    """
    if "0.0.0.1" in node.ip:
        # CHAIN DOES NOT ALLOW 127.0.0.1 TO BE POSTED. IS THIS
        # A REASONABLE WORKAROUND FOR LOCAL DEV?
        #if replace_with_docker_localhost:
        #    return f"http://host.docker.internal:{node.port}"
        #elif replace_with_localhost:
        return f"http://localhost:{node.port}"
    
    return f"http://{node.ip}:{node.port}"


async def make_non_streamed_get(
    httpx_client: httpx.AsyncClient,
    server_address: str,
    validator_ss58_address: str,
    symmetric_key_uuid: str,
    endpoint: str,
    timeout: float = 10,
):
    headers = _get_headers(symmetric_key_uuid, validator_ss58_address)
    logger.debug(f"headers: {headers}")
    response = await httpx_client.get(
        timeout=timeout,
        headers=headers,
        url=server_address + endpoint,
    )
    return response


async def make_non_streamed_post(
    httpx_client: httpx.AsyncClient,
    server_address: str,
    validator_ss58_address: str,
    fernet: Fernet,
    symmetric_key_uuid: str,
    endpoint: str,
    payload: dict[str, Any],
    timeout: float = 10,
) -> httpx.Response:
    headers = _get_headers(symmetric_key_uuid, validator_ss58_address)

    payload[cst.NONCE] = generate_nonce()
    encrypted_payload = fernet.encrypt(json.dumps(payload).encode())
    response = await httpx_client.post(
        content=encrypted_payload,   # NOTE: can this be content?
        timeout=timeout,
        headers=headers,
        url=server_address + endpoint,
    )
    return response


async def make_streamed_post(
    httpx_client: httpx.AsyncClient,
    server_address: str,
    validator_ss58_address: str,
    fernet: Fernet,
    symmetric_key_uuid: str,
    endpoint: str,
    payload: dict[str, Any],
    timeout: float = 10,
) -> AsyncGenerator[bytes, None]:
    headers = _get_headers(symmetric_key_uuid, validator_ss58_address)

    payload[cst.NONCE] = generate_nonce()
    encrypted_payload = fernet.encrypt(json.dumps(payload).encode())

    async with httpx_client.stream(
        method="POST",
        url=server_address + endpoint,
        content=encrypted_payload,  # NOTE: can this be content?
        headers=headers,
        timeout=timeout,
    ) as response:
        try:
            response.raise_for_status()
            async for line in response.aiter_raw():
                yield line
        except httpx.HTTPStatusError as e:
            await response.aread()
            logger.error(f"HTTP Error {e.response.status_code}: {e.response.text}")
            raise
        except Exception:
            # logger.error(f"Unexpected error: {str(e)}")
            # logger.exception("Full traceback:")
            raise
