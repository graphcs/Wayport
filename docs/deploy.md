# Deploying the relay to Railway

The relay is the only always-on piece. Both machines connect out to it, so
neither needs an open inbound port.

## One-time setup

```bash
railway login
railway init                       # or: railway link  (existing project)
```

Generate and set a token. Without one the relay serves anyone who finds the
URL, and an open relay on a public URL is someone else's free proxy:

```bash
railway variables --set "WAYPORT_RELAY_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
```

Deploy:

```bash
railway up
railway domain                     # assigns <name>.up.railway.app
```

Check it:

```bash
curl https://<name>.up.railway.app/health     # {"status": "ok"}
```

## Point the clients at it

Set `DEFAULT_RELAY_URL` in `src/wayport/common/defaults.py` to
`wss://<name>.up.railway.app` so neither machine needs `--relay-url`.

On each machine, export the same token the relay uses:

```bash
export WAYPORT_EXITNODE_RELAY_TOKEN=<token>    # machine sharing its internet
export WAYPORT_CLIENT_RELAY_TOKEN=<token>      # machine using the tunnel
```

Then:

```bash
wayport server                 # prints a connection code
wayport client <CODE>          # SOCKS5 proxy on 127.0.0.1:1080
```

## Things that will bite you

**`numReplicas` must stay 1.** Every session — connection codes, exit-node
registrations, pairings — lives in memory in `relay/session.py`. With two
replicas the exit node registers on one instance while the client's code lookup
lands on the other, and pairing fails about half the time. Horizontal scaling
needs Redis first.

**Don't enable app sleep / serverless.** Sleeping drops every registered exit
node's WebSocket and wipes the in-memory code table.

**`wss://`, not `ws://`.** Railway terminates TLS at its edge. `aiohttp`
handles `wss://` from the URL scheme with no code change, and
`normalize_relay_url` will rewrite a pasted `https://` dashboard URL for you.

**Redeploys drop connections, and that's fine.** Both sides reconnect
automatically and re-request their previous code, so the same code comes back
within a few seconds.

**Behind a TLS-intercepting proxy** (Zscaler and friends) `wss://` can fail
certificate verification where `ws://localhost` never did. `main()` calls
`truststore.inject_into_ssl()`, which uses the OS trust store where the
corporate root CA is already installed.

## Cost

Hobby plan, $5/month with $5 of usage included. An idle relay is roughly 120MB
of RAM and near-zero CPU — comfortably inside the included credit. The free
trial tier will not keep a long-lived WebSocket service alive reliably.
