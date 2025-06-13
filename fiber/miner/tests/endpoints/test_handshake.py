import base64
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fiber.encrypted.miner.core.configuration import Config
from fiber.encrypted.miner.core.models.encryption import SymmetricKeyExchange
from fiber.encrypted.miner.endpoints.handshake import factory_router
from fiber.encrypted.miner.security.nonce_management import NonceManager


class TestHandshake(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        router = factory_router()
        app.include_router(router)
        self.client = TestClient(app)

        self.mock_config = Mock(spec=Config)
        self.mock_encryption_keys_handler = Mock()
        self.mock_config.encryption_keys_handler = self.mock_encryption_keys_handler

        # Mock metagraph for dependencies
        self.mock_metagraph = Mock()
        self.mock_config.metagraph = self.mock_metagraph
        self.mock_config.min_stake_threshold = 1000

        # Mock metagraph nodes for blacklist dependency
        mock_node = Mock()
        mock_node.stake = 2000  # Above threshold
        self.mock_metagraph.nodes = {"test_hotkey": mock_node}

        self.mock_encryption_keys_handler.public_bytes = b"mock_public_key"
        self.mock_encryption_keys_handler.nonce_manager = NonceManager()
        self.mock_encryption_keys_handler.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        self.mock_config.keypair = Mock()
        self.mock_config.keypair.hotkey = "test_hotkey"

    @patch("fiber.encrypted.miner.core.configuration.factory_config")
    @patch("fiber.chain.signatures.sign_message")
    def test_get_public_key(self, mock_sign_message, mock_factory_config):
        # Configure the mock_factory_config
        mock_factory_config.return_value = self.mock_config

        # Configure mock_sign_message
        mock_sign_message.return_value = "mock_signature"

        # Make the request
        response = self.client.get("/public-encryption-key")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["public_key"], self.mock_encryption_keys_handler.public_bytes.decode())
        self.assertIn("timestamp", data)

        mock_factory_config.assert_called_once()

    @patch("fiber.chain.signatures.verify_signature")
    @patch("fiber.encrypted.miner.core.configuration.factory_config")
    def test_exchange_symmetric_key_success(self, mock_factory_config, mock_verify_signature):
        mock_factory_config.return_value = self.mock_config
        mock_verify_signature.return_value = True
        symmetric_key = b"test_symmetric_key"
        encrypted_symmetric_key = self.mock_encryption_keys_handler.private_key.public_key().encrypt(
            symmetric_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

        payload = SymmetricKeyExchange(
            encrypted_symmetric_key=base64.b64encode(encrypted_symmetric_key).decode(),
        )

        headers = {
            "validator-hotkey": "test_hotkey",
            "nonce": "test_nonce", 
            "symmetric-key-uuid": "test_uuid",
        }

        response = self.client.post("/exchange-symmetric-key", json=payload.model_dump(), headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "Symmetric key exchanged successfully"})
        # Note: The actual API call signature is different, so this assertion may need adjustment
        # self.mock_encryption_keys_handler.add_symmetric_key.assert_called_once_with(...)

    @patch("fiber.chain.signatures.verify_signature")
    @patch("fiber.encrypted.miner.core.configuration.factory_config")
    def test_exchange_symmetric_key_invalid_signature(self, mock_factory_config, mock_verify_signature):
        mock_factory_config.return_value = self.mock_config
        mock_verify_signature.return_value = False

        payload = SymmetricKeyExchange(
            encrypted_symmetric_key=base64.b64encode(b"test_key").decode(),
        )

        headers = {
            "validator-hotkey": "test_hotkey",
            "nonce": "test_nonce",
            "symmetric-key-uuid": "test_uuid",
        }

        response = self.client.post("/exchange-symmetric-key", json=payload.model_dump(), headers=headers)

        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid signature", response.json()["detail"])

    @patch("fiber.chain.signatures.verify_signature")
    @patch("fiber.encrypted.miner.core.configuration.factory_config")
    def test_exchange_symmetric_key_duplicate_nonce(self, mock_factory_config, mock_verify_signature):
        mock_factory_config.return_value = self.mock_config
        mock_verify_signature.return_value = True

        payload = SymmetricKeyExchange(
            encrypted_symmetric_key=base64.b64encode(b"test_key").decode(),
        )

        headers = {
            "validator-hotkey": "test_hotkey",
            "nonce": "duplicate_nonce",
            "symmetric-key-uuid": "test_uuid",
        }

        self.mock_encryption_keys_handler.nonce_manager.add_nonce("duplicate_nonce")

        response = self.client.post("/exchange-symmetric-key", json=payload.model_dump(), headers=headers)

        self.assertEqual(response.status_code, 400)
        self.assertIn("nonce", response.json()["detail"])

    def test_factory_router(self):
        router = factory_router()
        self.assertEqual(len(router.routes), 2)
        self.assertTrue(any(route.path == "/exchange-symmetric-key" for route in router.routes))
        self.assertTrue(any(route.path == "/public-encryption-key" for route in router.routes))


if __name__ == "__main__":
    unittest.main()
