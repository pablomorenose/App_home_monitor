from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
from base64 import urlsafe_b64encode

private_key = ec.generate_private_key(ec.SECP256R1())
public_key = private_key.public_key()

priv_bytes = private_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
pub_bytes = public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)

print("VAPID_PRIVATE_KEY=" + urlsafe_b64encode(priv_bytes).decode().rstrip('='))
print("VAPID_PUBLIC_KEY="  + urlsafe_b64encode(pub_bytes).decode().rstrip('='))
