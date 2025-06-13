import asyncio
import time
import ujson as json
from pathlib import Path
from typing import Any, Dict

from substrateinterface import Keypair

from fiber import SubstrateInterface
from fiber.logging_utils import get_logger
from fiber import constants as fcst

logger = get_logger(__name__)

# Global state for connection management
_connection_locks: Dict[int, asyncio.Lock] = {}
_connection_stats = {
    "reconnections_total": 0,
    "health_checks_passed": 0,
    "health_checks_failed": 0,
    "consecutive_failures": {},
}


def format_error_message(error_message: dict | None) -> str:
    err_type, err_name, err_description = (
        "UnknownType",
        "UnknownError",
        "Unknown Description",
    )
    if isinstance(error_message, dict):
        err_type = error_message.get("type", err_type)
        err_name = error_message.get("name", err_name)
        err_description = error_message.get("docs", [err_description])[0]
    return f"substrate returned `{err_name} ({err_type})` error. Description: `{err_description}`"


def get_hotkey_file_path(wallet_name: str, hotkey_name: str) -> Path:
    file_path = Path.home() / ".bittensor" / "wallets" / wallet_name / "hotkeys" / hotkey_name
    return file_path


def get_coldkeypub_file_path(wallet_name: str) -> Path:
    file_path = Path.home() / ".bittensor" / "wallets" / wallet_name / "coldkeypub.txt"
    return file_path


def load_coldkeypub_keypair(wallet_name: str) -> Keypair:
    file_path = get_coldkeypub_file_path(wallet_name)
    try:
        with open(file_path, "r") as file:
            keypair_data = json.load(file)
        keypair = Keypair(ss58_address=keypair_data["ss58Address"])
        logger.info(f"Loaded keypair from {file_path}")
        return keypair
    except Exception as e:
        raise ValueError(f"Failed to load keypair: {str(e)}")


def load_hotkey_keypair(wallet_name: str, hotkey_name: str) -> Keypair:
    file_path = get_hotkey_file_path(wallet_name, hotkey_name)
    try:
        with open(file_path, "r") as file:
            keypair_data = json.load(file)
        keypair = Keypair.create_from_seed(keypair_data["secretSeed"])
        logger.info(f"Loaded keypair from {file_path}")
        return keypair
    except Exception as e:
        raise ValueError(f"Failed to load keypair: {str(e)}")


def sign_message(keypair: Keypair, message: str | None) -> str | None:
    if message is None:
        return None
    return f"0x{keypair.sign(message).hex()}"


def query_substrate(
    substrate: SubstrateInterface,
    module: str,
    method: str,
    params: list[Any],
    return_value: bool = True,
    block: int | None = None,
) -> tuple[SubstrateInterface, Any]:
    try:
        block_hash = substrate.get_block_hash(block) if block is not None else None
        query_result = substrate.query(module, method, params, block_hash=block_hash)

        return_val = query_result.value if return_value else query_result

        return substrate, return_val
    except Exception as e:
        logger.error(f"Substrate query failed with error: {e}. Reconnecting and retrying.")

        # Use the centralized reconnection utility
        substrate = reconnect_substrate(substrate, raise_on_failure=False)

        block_hash = substrate.get_block_hash(block) if block is not None else None
        query_result = substrate.query(module, method, params, block_hash=block_hash)

        return_val = query_result.value if return_value else query_result

        return substrate, return_val


def reconnect_substrate(
    old_substrate: SubstrateInterface, 
    raise_on_failure: bool = False,
    health_check_timeout: float = 3.0,
    max_consecutive_failures: int = 3,
    backoff_base: float = 2.0
) -> SubstrateInterface:
    """
    Check if substrate connection is healthy, and recreate it if needed.
    Includes circuit breaker, basic concurrency protection, and metrics.
    
    Args:
        old_substrate: The existing substrate connection to check/recreate
        raise_on_failure: If True, raise exception on recreation failure
        health_check_timeout: Timeout for health check in seconds
        max_consecutive_failures: Max failures before circuit breaker kicks in
        backoff_base: Base for exponential backoff (seconds)
    
    Returns:
        The existing SubstrateInterface if healthy, a new one if recreation succeeds,
        or the old one if recreation fails and raise_on_failure is False.
    
    Raises:
        Exception: If recreation fails and raise_on_failure is True
    """
    global _connection_stats
    
    old_url = old_substrate.url
    
    # Check circuit breaker
    consecutive_failures = _connection_stats["consecutive_failures"].get(old_url, 0)
    if consecutive_failures >= max_consecutive_failures:
        backoff_time = min(backoff_base ** consecutive_failures, 60)  # Cap at 60 seconds
        logger.warning(f"Circuit breaker active for {old_url}. Waiting {backoff_time:.1f}s before retry...")
        time.sleep(backoff_time)
    
    # First, check if the existing connection still works
    try:
        # Simple timeout mechanism for sync operation
        start_time = time.perf_counter()
        old_substrate.get_block_hash(None)
        if time.perf_counter() - start_time > health_check_timeout:
            raise TimeoutError(f"Health check exceeded {health_check_timeout}s")
            
        logger.debug("Existing substrate connection is healthy, no reconnection needed")
        _connection_stats["health_checks_passed"] += 1
        # Reset failure counter on success
        _connection_stats["consecutive_failures"][old_url] = 0
        return old_substrate
        
    except Exception as test_error:
        logger.warning(f"Existing substrate connection is unhealthy: {test_error}. Attempting reconnection...")
        _connection_stats["health_checks_failed"] += 1
        _connection_stats["consecutive_failures"][old_url] = consecutive_failures + 1

    # Connection is unhealthy, attempt to recreate
    try:
        # Create new connection with ALL necessary parameters
        new_substrate = SubstrateInterface(
            url=old_url,
            ss58_format=fcst.SS58_FORMAT,  # Default is 42
            use_remote_preset=True,
        )
        
        # Test the new connection to ensure it works
        start_time = time.perf_counter()
        new_substrate.get_block_hash(None)
        if time.perf_counter() - start_time > health_check_timeout:
            raise TimeoutError(f"New connection test exceeded {health_check_timeout}s")
        
        # Only close old connection if new one is working
        try:
            old_substrate.close()
        except Exception as close_error:
            logger.warning(f"Failed to close old substrate connection: {close_error}")
        
        logger.info("Successfully recreated substrate connection")
        _connection_stats["reconnections_total"] += 1
        # Reset failure counter on successful reconnection
        _connection_stats["consecutive_failures"][old_url] = 0
            
        return new_substrate
        
    except Exception as recreation_error:
        logger.error(f"Failed to recreate substrate connection: {recreation_error}")
        
        if raise_on_failure:
            raise recreation_error
        
        # If we can't recreate the connection, continue with the old one
        logger.warning("Continuing with existing (unhealthy) substrate connection")
        return old_substrate


def get_substrate_connection_stats() -> Dict[str, Any]:
    """Get connection statistics for monitoring."""
    return dict(_connection_stats)
