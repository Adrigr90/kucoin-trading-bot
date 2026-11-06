"""Read-only access to KuCoin public market data (spec 02 §2, §3.1 and §3.2).

REST is limited to GET requests on a whitelist of routes; the WebSocket has its own
whitelist of messages. No key, no token and no private or order channel is ever used.
"""
