use anyhow::{anyhow, Result};
use chacha20poly1305::{
    aead::{Aead, KeyInit},
    ChaCha20Poly1305, Nonce,
};
use ed25519_dalek::{SecretKey, SigningKey, VerifyingKey, Signature, Signer, Verifier};
use hkdf::Hkdf;
use rand::Rng;
use sha2::Sha256;
use zeroize::Zeroize;

const NONCE_SIZE: usize = 12;
const CHACHA_KEY_SIZE: usize = 32;

/// Ed25519 private key wrapper
#[derive(Clone)]
pub struct Ed25519Key {
    key: SigningKey,
}

impl Ed25519Key {
    /// Generate a new random Ed25519 key
    pub fn generate() -> Self {
        let mut rng = rand::thread_rng();
        let mut secret_bytes = [0u8; 32];
        rng.fill(&mut secret_bytes);
        let secret_key = SecretKey::from(secret_bytes);
        let key = SigningKey::from(secret_key);
        Ed25519Key { key }
    }

    /// Create from raw bytes
    pub fn from_bytes(bytes: &[u8; 32]) -> Result<Self> {
        let secret_key = SecretKey::from(*bytes);
        let key = SigningKey::from(secret_key);
        Ok(Ed25519Key { key })
    }

    /// Get the public key
    pub fn public_key(&self) -> Vec<u8> {
        self.key.verifying_key().to_bytes().to_vec()
    }

    /// Sign a message
    pub fn sign(&self, message: &[u8]) -> Vec<u8> {
        self.key.sign(message).to_bytes().to_vec()
    }

    /// Get raw bytes (for storage)
    pub fn to_bytes(&self) -> Vec<u8> {
        self.key.to_bytes().to_vec()
    }
}

/// Verify an Ed25519 signature
pub fn verify_signature(
    public_key_bytes: &[u8],
    message: &[u8],
    signature_bytes: &[u8],
) -> Result<()> {
    let public_key = VerifyingKey::from_bytes(
        <&[u8; 32]>::try_from(public_key_bytes)
            .map_err(|_| anyhow!("Invalid public key size"))?,
    )
    .map_err(|e| anyhow!("Invalid public key: {}", e))?;

    let signature = Signature::from_bytes(
        <&[u8; 64]>::try_from(signature_bytes)
            .map_err(|_| anyhow!("Invalid signature size"))?,
    );

    public_key.verify(message, &signature)?;
    Ok(())
}

/// ChaCha20-Poly1305 encryption key
#[derive(Clone)]
pub struct ChaChaKey {
    key: [u8; CHACHA_KEY_SIZE],
}

impl Drop for ChaChaKey {
    fn drop(&mut self) {
        self.key.zeroize();
    }
}

impl ChaChaKey {
    /// Generate a new random key
    pub fn generate() -> Self {
        let mut rng = rand::thread_rng();
        let mut key = [0u8; CHACHA_KEY_SIZE];
        rng.fill(&mut key);
        ChaChaKey { key }
    }

    /// Create from raw bytes
    pub fn from_bytes(bytes: &[u8; CHACHA_KEY_SIZE]) -> Self {
        let mut key = [0u8; CHACHA_KEY_SIZE];
        key.copy_from_slice(bytes);
        ChaChaKey { key }
    }

    /// Get raw bytes
    pub fn as_bytes(&self) -> &[u8; CHACHA_KEY_SIZE] {
        &self.key
    }
}

/// Encrypt a message with ChaCha20-Poly1305
pub fn encrypt_chacha(key: &ChaChaKey, nonce_u64: u64, plaintext: &[u8]) -> Result<Vec<u8>> {
    let cipher = ChaCha20Poly1305::new(key.key.as_ref().into());

    // Convert u64 nonce to 12-byte nonce (IETF variant)
    let mut nonce_bytes = [0u8; NONCE_SIZE];
    nonce_bytes[4..].copy_from_slice(&nonce_u64.to_le_bytes());
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ciphertext = cipher
        .encrypt(nonce, plaintext)
        .map_err(|e| anyhow!("Encryption failed: {}", e))?;

    Ok(ciphertext)
}

/// Decrypt a message with ChaCha20-Poly1305
pub fn decrypt_chacha(key: &ChaChaKey, nonce_u64: u64, ciphertext: &[u8]) -> Result<Vec<u8>> {
    let cipher = ChaCha20Poly1305::new(key.key.as_ref().into());

    // Convert u64 nonce to 12-byte nonce
    let mut nonce_bytes = [0u8; NONCE_SIZE];
    nonce_bytes[4..].copy_from_slice(&nonce_u64.to_le_bytes());
    let nonce = Nonce::from_slice(&nonce_bytes);

    let plaintext = cipher
        .decrypt(nonce, ciphertext)
        .map_err(|e| anyhow!("Decryption failed: {}", e))?;

    Ok(plaintext)
}

/// Derive a session key using HKDF-SHA256
pub fn kdf_derive_session_key(
    dh_secret: &[u8],
    attestation_report: &[u8],
) -> Result<ChaChaKey> {
    let hkdf = Hkdf::<Sha256>::new(Some(attestation_report), dh_secret);
    let mut key_bytes = [0u8; CHACHA_KEY_SIZE];
    hkdf.expand(b"amf-wg-session", &mut key_bytes)
        .map_err(|e| anyhow!("HKDF expansion failed: {}", e))?;

    Ok(ChaChaKey::from_bytes(&key_bytes))
}

/// Compute HMAC-SHA256 (for archive integrity verification)
pub fn hmac_sha256(key: &[u8], message: &[u8]) -> Vec<u8> {
    use sha2::{Sha256, Digest};

    // Simple HMAC: HMAC(K, m) = H((K ⊕ opad) ∥ H((K ⊕ ipad) ∥ m))
    let block_size = 64; // SHA256 block size
    let mut ipad = vec![0x36u8; block_size];
    let mut opad = vec![0x5cu8; block_size];

    let key_padded = if key.len() > block_size {
        let mut h = Sha256::new();
        h.update(key);
        let digest = h.finalize();
        let mut padded = vec![0u8; block_size];
        padded[..digest.len()].copy_from_slice(&digest);
        padded
    } else {
        let mut padded = vec![0u8; block_size];
        padded[..key.len()].copy_from_slice(key);
        padded
    };

    for (i, byte) in key_padded.iter().enumerate() {
        ipad[i] ^= byte;
        opad[i] ^= byte;
    }

    let mut inner = Sha256::new();
    inner.update(&ipad);
    inner.update(message);
    let inner_hash = inner.finalize();

    let mut outer = Sha256::new();
    outer.update(&opad);
    outer.update(&inner_hash);

    outer.finalize().to_vec()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ed25519_sign_verify() {
        let key = Ed25519Key::generate();
        let message = b"test message";
        let signature = key.sign(message);
        let public_key = key.public_key();

        assert!(verify_signature(&public_key, message, &signature).is_ok());
    }

    #[test]
    fn test_chacha_encrypt_decrypt() {
        let key = ChaChaKey::generate();
        let plaintext = b"secret data";
        let nonce = 42u64;

        let ciphertext = encrypt_chacha(&key, nonce, plaintext).unwrap();
        let decrypted = decrypt_chacha(&key, nonce, &ciphertext).unwrap();

        assert_eq!(plaintext, decrypted.as_slice());
    }

    #[test]
    fn test_hkdf_derivation() {
        let dh_secret = b"shared secret from DH exchange";
        let attestation = b"TEE attestation report";

        let key1 = kdf_derive_session_key(dh_secret, attestation).unwrap();
        let key2 = kdf_derive_session_key(dh_secret, attestation).unwrap();

        assert_eq!(key1.as_bytes(), key2.as_bytes());
    }
}
