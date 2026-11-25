

def parse_port(raw: str) -> int:
    """Parse a port number from a raw connection setting."""
    try:
        return int(raw)
    except Exception:
        return 0
