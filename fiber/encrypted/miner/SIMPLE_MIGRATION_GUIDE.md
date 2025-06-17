# Simple Encryption Key Migration Guide

## Overview
This simplified migration allows you to gradually transition miners from the default encryption key to a custom one without breaking existing validator connections.

## How It Works
The miner will:
1. Load existing keys using the current encryption (default if `STORAGE_ENCRYPTION_KEY` not set)
2. Save all keys using the `MIGRATE_TO_KEY` if set, otherwise use current encryption
3. When loading, try current key first, then migration key if different

## Migration Steps

### Step 1: Choose Your Migration Key
Generate a secure Fernet key:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```
Example: `_wRQvMjCg7zeuOmJ11ippMs40XnD7rDMo8Jleyt-tDY=`

### Step 2: Update Existing Machine
On your current ASGI proxy machine:
```bash
# Set the migration target key
export MIGRATE_TO_KEY="_wRQvMjCg7zeuOmJ11ippMs40XnD7rDMo8Jleyt-tDY="

# Restart miners - they will start saving with the new key
# but still load with the old key
```

### Step 3: Wait for Key Rotation
- Let miners run for a while (hours/days)
- New validator handshakes will be saved with the migration key
- Existing validators keep working

### Step 4: Copy to New Machine
```bash
# Copy all encrypted files to new machine
scp {hotkey}_symmetric_keys.encrypted new-machine:/path/
```

### Step 5: Start on New Machine
On the new machine:
```bash
# Set the storage key to your migration key
export STORAGE_ENCRYPTION_KEY="_wRQvMjCg7zeuOmJ11ippMs40XnD7rDMo8Jleyt-tDY="

# Start miners - they will use the migration key for everything
```

## Key Points
- No tracking files needed
- Single encrypted file for all validators
- Gradual transition as validators reconnect
- Zero downtime migration 