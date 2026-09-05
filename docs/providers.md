# Metadata providers

## Audible

Primary provider abstraction. The default marketplace is `us` / audible.com. German remains supported through locale `de` / audible.de. The current implementation is a deterministic stub so tests do not require credentials.

A real provider should use registered-device/account tokens or a proven library, store credentials via `FILE__` secrets, and never log OAuth redirect URLs or tokens.

## Audnexus

Fallback community metadata provider, default base URL `https://api.audnex.us`. Tests use mocked HTTP responses.
