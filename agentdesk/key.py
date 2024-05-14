from dataclasses import dataclass, field
from typing import List, Optional, Dict
import uuid
import time
import os
import base64
import json
import io
from pathlib import Path

import paramiko
from cryptography.fernet import Fernet

from agentdesk.db.models import SSHKeyRecord
from agentdesk.db.conn import WithDB
from agentdesk.server.models import V1SSHKey


@dataclass
class SSHKeyPair(WithDB):
    """An SSH key"""

    name: str
    public_key: str
    private_key: str
    owner_id: str
    created: float = field(default_factory=lambda: time.time())
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.private_key = self.encrypt_private_key(self.private_key)
        self.save()

    @classmethod
    def get_encryption_key(cls) -> bytes:
        # Step 1: Try to get the key from an environment variable
        key = os.getenv("ENCRYPTION_KEY")
        if key:
            return key.encode()

        # Define the path for the local encryption key file
        key_path = Path.home() / ".agentsea/keys/agentdesk_encryption_key"

        # Step 2: Try to get the key from a local file
        try:
            if key_path.exists():
                with key_path.open("rb") as file:
                    return file.read()
        except IOError as e:
            print(f"Failed to read the encryption key from {key_path}: {e}")

        print(
            "No encryption key found. Generating a new one. "
            "This key will be stored in ~/.agentsea/keys/agentdesk_encryption_key"
        )
        # Step 3: Generate a new key and store it if neither of the above worked
        key = Fernet.generate_key()
        try:
            key_path.parent.mkdir(
                parents=True, exist_ok=True
            )  # Ensure the directory exists
            with key_path.open("wb") as file:
                file.write(key)
        except IOError as e:
            print(f"Failed to write the new encryption key to {key_path}: {e}")
            raise Exception("Failed to secure an encryption key.")

        return key

    def encrypt_private_key(self, private_key: str) -> str:
        key = self.get_encryption_key()
        fernet = Fernet(key)
        encrypted_private_key = fernet.encrypt(private_key.encode())
        return base64.b64encode(encrypted_private_key).decode()

    @classmethod
    def decrypt_private_key(cls, encrypted_private_key: str) -> str:
        key = cls.get_encryption_key()
        fernet = Fernet(key)
        decrypted_private_key = fernet.decrypt(base64.b64decode(encrypted_private_key))
        return decrypted_private_key.decode()

    @classmethod
    def generate_key(
        cls,
        name: str,
        owner_id: str,
        passphrase: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> "SSHKeyPair":
        """
        Generates a new SSH key pair using Paramiko. Encrypts the private key with a passphrase if provided.
        Returns an instance of SSHKey with the encrypted private key and public key.
        """
        key = paramiko.RSAKey.generate(2048)
        private_key_io = io.StringIO()
        key.write_private_key(private_key_io, password=passphrase)
        private_key = private_key_io.getvalue()
        public_key = f"{key.get_name()} {key.get_base64()}"

        return cls(
            name=name,
            owner_id=owner_id,
            public_key=public_key,
            private_key=private_key,
            metadata=metadata or {},
        )

    def to_record(self) -> SSHKeyRecord:
        return SSHKeyRecord(
            id=self.id,
            owner_id=self.owner_id,
            name=self.name,
            public_key=self.public_key,
            private_key=self.private_key,
            created=self.created,
            metadata_=json.dumps(self.metadata),
            full_name=f"{self.owner_id}/{self.name}",
        )

    @classmethod
    def from_record(cls, record: SSHKeyRecord) -> "SSHKeyPair":
        obj = cls.__new__(cls)
        obj.id = str(record.id)
        obj.public_key = record.public_key  # type: ignore
        obj.private_key = record.private_key  # type: ignore
        obj.name = str(record.name)
        obj.created = record.created  # type: ignore
        obj.owner_id = record.owner_id  # type: ignore
        obj.metadata = json.loads(str(record.metadata_))
        return obj

    def save(self) -> None:
        for db in self.get_db():
            db.merge(self.to_record())
            db.commit()

    @classmethod
    def find(cls, **kwargs) -> List["SSHKeyPair"]:
        for db in cls.get_db():
            records = db.query(SSHKeyRecord).filter_by(**kwargs).all()
            return [cls.from_record(record) for record in records]

        raise Exception("no session")

    @classmethod
    def find_name_starts_like(cls, name: str) -> List["SSHKeyPair"]:
        """
        Find SSHKeyPair instances where the name field matches the given pattern.
        """
        for db in cls.get_db():
            name_pattern = name + "%"
            records = (
                db.query(SSHKeyRecord)
                .filter(SSHKeyRecord.name.like(name_pattern))
                .all()
            )
            return [cls.from_record(record) for record in records]

        raise Exception("No database session available")

    @classmethod
    def delete(cls, name: str, owner_id: str) -> None:
        for db in cls.get_db():
            record = (
                db.query(SSHKeyRecord).filter_by(name=name, owner_id=owner_id).first()
            )
            if record:
                db.delete(record)
                db.commit()

    def to_v1(self) -> V1SSHKey:
        return V1SSHKey(
            id=self.id,
            public_key=self.public_key,
            name=self.name,
            created=self.created,
            private_key=self.decrypt_private_key(self.private_key),
        )
