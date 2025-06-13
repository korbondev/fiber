from async_substrate_interface import SubstrateInterface
from scalecodec.utils.ss58 import ss58_encode
from tenacity import retry, stop_after_attempt, wait_exponential

from fiber import constants as fcst
from fiber.chain import chain_utils as chain_utils
from fiber.chain import models
from fiber.chain.interface import get_substrate
from fiber.chain.models import Node
from fiber.chain.chain_utils import reconnect_substrate
from fiber.logging_utils import get_logger

logger = get_logger(__name__)


def _ss58_encode(address: list[int] | list[list[int]], ss58_format: int = fcst.SS58_FORMAT) -> str:
    if not isinstance(address[0], int):
        address = address[0]
    return ss58_encode(bytes(address).hex(), ss58_format)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4))
def _get_nodes_for_uid(substrate: SubstrateInterface, netuid: int, block: int | None = None):
    if block is not None:
        block_hash = substrate.get_block_hash(block)
    else:
        block_hash = None

    response = substrate.runtime_call(
        api="SubnetInfoRuntimeApi",
        method="get_metagraph",
        params=[netuid],
        block_hash=block_hash,
    )
    metagraph = response.value

    nodes = []

    for uid in range(len(metagraph["hotkeys"])):
        axon = metagraph["axons"][uid]

        node = Node(
            hotkey=_ss58_encode(metagraph["hotkeys"][uid], fcst.SS58_FORMAT),
            coldkey=_ss58_encode(metagraph["coldkeys"][uid], fcst.SS58_FORMAT),
            node_id=uid,
            incentive=metagraph["incentives"][uid],
            netuid=metagraph["netuid"],
            alpha_stake=metagraph["alpha_stake"][uid] * 10**-9,
            tao_stake=metagraph["tao_stake"][uid] * 10**-9,
            stake=metagraph["total_stake"][uid] * 10**-9,
            trust=metagraph["trust"][uid],
            vtrust=metagraph["consensus"][uid],
            last_updated=float(metagraph["last_update"][uid]),
            ip=str(axon["ip"]),
            ip_type=axon["ip_type"],
            port=axon["port"],
            protocol=axon["protocol"],
        )
        nodes.append(node)

    return nodes


def get_nodes_for_netuid(substrate: SubstrateInterface, netuid: int, block: int | None = None) -> list[models.Node]:
    # Try to use the existing substrate connection first
    try:
        return _get_nodes_for_uid(substrate, netuid, block)
    except Exception as e:
        logger.warning(f"Failed to fetch nodes with existing substrate connection: {e}. Creating new connection.")
        
        # Use the centralized reconnection utility
        substrate = reconnect_substrate(substrate, raise_on_failure=False)
        
        try:
            return _get_nodes_for_uid(substrate, netuid, block)
        except Exception as retry_error:
            logger.error(f"Failed even after reconnection attempt: {retry_error}")
            # Re-raise the original error with context
            raise e from retry_error
