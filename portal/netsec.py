"""Network-security helpers: who is really connecting, which networks may use the staff area / POS, and a
streaming request-size limit.  Pure Python (no web framework) so every rule here is unit-tested without a server.

Why this file exists (v6):
  * "Is this request from the internet?" used to be answered ONLY by looking for Cloudflare headers.  If a tunnel
    ever forgot them, internet visitors looked like the shop network.  The staff area now also checks the TCP
    peer address, the port the request arrived on (JCM_PUBLIC_PORTS) and an optional list of allowed networks.
  * Proxy headers (CF-Connecting-IP / X-Forwarded-For) are only believed when they come from a trusted proxy
    (the tunnel program on this PC), so a customer on the shop Wi-Fi cannot fake an address to dodge rate limits.
  * The upload size limit used to trust the Content-Length header; a chunked upload has none.  limited_receive()
    counts the real bytes.
"""
import ipaddress

DEFAULT_TRUSTED = ("127.0.0.1", "::1")


class BodyTooLarge(Exception):
    """Raised inside the request body reader when more bytes arrive than allowed."""


def ip_of(text):
    """'10.0.4.5' / '::ffff:10.0.4.5' -> ip_address object (IPv4-mapped IPv6 unwrapped), or None."""
    try:
        a = ipaddress.ip_address(str(text or "").strip().strip("[]"))
    except ValueError:
        return None
    mapped = getattr(a, "ipv4_mapped", None)
    return mapped if mapped else a


def is_loopback(peer) -> bool:
    a = ip_of(peer)
    return bool(a and a.is_loopback)


def is_private(peer) -> bool:
    """Loopback, RFC1918 (192.168.x, 10.x, 172.16-31.x), link-local, IPv6 unique-local."""
    a = ip_of(peer)
    return bool(a and (a.is_private or a.is_loopback or a.is_link_local))


def parse_nets(items):
    """['10.0.4.0/24', '127.0.0.1'] -> list of networks. Bad entries are skipped (see peer_in for what that means)."""
    out = []
    if isinstance(items, str):
        items = [x for x in items.replace(";", ",").split(",")]
    for x in items or []:
        s = str(x).strip()
        if not s:
            continue
        try:
            out.append(ipaddress.ip_network(s, strict=False))
        except ValueError:
            continue
    return out


def peer_in(peer, items) -> bool:
    """True when `items` is empty (no restriction) or the peer is inside one of the listed networks.
    A list that has entries but none of them valid denies everyone (a typo must never open the door)."""
    if not items:
        return True
    nets = parse_nets(items)
    if not nets:
        return False
    a = ip_of(peer)
    if a is None:
        return False
    return any(a.version == n.version and a in n for n in nets)


def trusted_proxy(peer, trusted=None) -> bool:
    """Is the TCP peer a proxy whose X-Forwarded-For / CF-Connecting-IP we may believe?"""
    return peer_in(peer, list(trusted) if trusted else list(DEFAULT_TRUSTED))


def real_client_ip(peer: str, header, proxied: bool, trusted=None) -> str:
    """The visitor's address.  `header(name)` returns a request header or ''.  Headers are believed only when the
    request carries proxy headers AND comes from a trusted proxy; the value must be a real IP address."""
    if proxied and trusted_proxy(peer, trusted):
        cand = (header("cf-connecting-ip") or "").strip()
        if not cand:
            xff = [p.strip() for p in (header("x-forwarded-for") or "").split(",") if p.strip()]
            cand = xff[-1] if xff else ""      # the entry our own proxy appended, not one the visitor typed
        a = ip_of(cand)
        if a is not None:
            return str(a)
    return peer or "?"


def limited_receive(receive, max_bytes: int, state: dict):
    """Wrap an ASGI `receive` so reading more than max_bytes of request body raises BodyTooLarge.
    state = {"seen": 0, "exceeded": False} is updated so the caller can tell what happened."""
    async def inner():
        msg = await receive()
        if msg.get("type") == "http.request":
            state["seen"] = state.get("seen", 0) + len(msg.get("body") or b"")
            if state["seen"] > max_bytes:
                state["exceeded"] = True
                raise BodyTooLarge()
        return msg
    return inner
